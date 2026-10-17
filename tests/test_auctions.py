"""Auctions: a starting bid, bids held in escrow, outbid refunds, and the sale when time is up, on every database."""

import pytest

from terraforma import wallclock
from terraforma.delivery import service as delivery
from terraforma.escrow.models import REFUNDED, RELEASED, Escrow
from terraforma.marketplace import service
from terraforma.marketplace.hooks import Marketplace
from terraforma.marketplace.models import EXPIRED, OPEN, SOLD, Listing

from .test_marketplace import DAY, ECONOMY, FEES, STORAGE, a_market, clock, gold_of  # noqa: F401
from .test_trading import account, holding, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

HOOKS = Marketplace()


async def auctioned(db, aria, start=100, items=((0, 2),)):
    return await service.post(db, ECONOMY, FEES, HOOKS, aria, "vanguard", list(items), start, auction=True)


async def bid(db, hero, listing, amount, to_guild="vanguard"):
    return await service.bid(db, ECONOMY, FEES, HOOKS, STORAGE, hero, listing.id, amount, to_guild)


async def third_team(db):
    owner = await account(db, "Dora")
    team, (dora,) = await team_with(db, owner, "Dorians", ["Dora"])
    await ECONOMY.credit_team(db, team.id, 500)
    return team, dora


async def test_an_auction_runs_two_days_and_takes_gold_only(clock, db):
    _mike, _cole_owner, mine, _yours, aria, _cole = await a_market(db)
    listing = await auctioned(db, aria)
    assert (listing.kind, listing.state, listing.price_gold) == ("auction", OPEN, 100)
    assert listing.expires_at == wallclock.timestamp() + 2 * DAY
    assert await gold_of(db, mine) == 95
    for gold, tokens in ((0, 0), (10, 5)):
        with pytest.raises(service.MarketplaceError, match="some gold"):
            await service.post(db, ECONOMY, FEES, HOOKS, aria, "vanguard", [(0, 1)], gold, tokens, auction=True)


async def test_bids_are_held_and_the_one_outbid_gets_everything_back(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    dora_team, dora = await third_team(db)
    listing = await auctioned(db, aria)
    await bid(db, cole, listing, 100)
    assert await gold_of(db, yours) == 100  # 200, less the 100 held
    with pytest.raises(service.MarketplaceError, match="at least 105"):  # the increment is a twentieth
        await bid(db, dora, listing, 104)
    first_escrow = listing.bid_escrow_id
    await bid(db, dora, listing, 105)
    assert await gold_of(db, yours) == 200 and await gold_of(db, dora_team) == 395
    await db.refresh(listing)
    assert (listing.bid_gold, listing.bid_team_id) == (105, dora_team.id)
    assert (await db.get(Escrow, first_escrow)).state == REFUNDED


async def test_a_team_can_raise_its_own_bid_and_not_bid_on_its_own_auction(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    listing = await auctioned(db, aria)
    with pytest.raises(service.MarketplaceError, match="own auction"):
        await bid(db, aria, listing, 100)
    await bid(db, cole, listing, 150)
    await bid(db, cole, listing, 160)
    assert await gold_of(db, yours) == 40  # only the latest bid is held


async def test_a_bid_that_cannot_be_made_changes_nothing(clock, db):
    _mike, _cole_owner, _mine, yours, aria, cole = await a_market(db)
    listing = await auctioned(db, aria)
    for amount, guild, match in ((99, "vanguard", "at least 100"), (10**10, "vanguard", "at least"), (100, "", "which guild"), (900, "vanguard", "not enough gold")):
        with pytest.raises(service.MarketplaceError, match=match):
            await bid(db, cole, listing, amount, guild)
    await db.refresh(listing)
    assert listing.bid_gold == 0 and await gold_of(db, yours) == 200
    with pytest.raises(service.MarketplaceError, match="gone"):
        await bid(db, cole, await service.post(db, ECONOMY, FEES, HOOKS, aria, "vanguard", [(0, 1)], 10), 20)  # a fixed-price listing
    clock.move(3 * DAY)
    with pytest.raises(service.MarketplaceError, match="gone"):
        await bid(db, cole, listing, 100)


async def test_a_fixed_price_cannot_be_bought_off_an_auction_and_a_bid_blocks_cancelling(clock, db):
    _mike, _cole_owner, _mine, _yours, aria, cole = await a_market(db)
    listing = await auctioned(db, aria)
    with pytest.raises(service.MarketplaceError, match="bid on it"):
        await service.buy(db, ECONOMY, FEES, HOOKS, cole, listing.id, "vanguard")
    await bid(db, cole, listing, 100)
    with pytest.raises(service.MarketplaceError, match="cannot be taken back"):
        await service.cancel(db, ECONOMY, STORAGE, aria, listing.id)


async def test_an_auction_with_no_bid_goes_back_to_its_seller_when_time_is_up(clock, db):
    _mike, _cole_owner, _mine, _yours, aria, _cole = await a_market(db)
    listing = await auctioned(db, aria)
    assert await service.expire_due(db, ECONOMY, STORAGE) == 0
    clock.move(2 * DAY)
    assert await service.expire_due(db, ECONOMY, STORAGE) == 1
    await db.refresh(listing)
    assert listing.state == EXPIRED and await holding(db, aria, "potion") == 2


async def test_the_highest_bidder_wins_the_goods_as_a_parcel_and_the_seller_is_paid(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    listing = await auctioned(db, aria)
    await bid(db, cole, listing, 120, "rearguard")  # the road to the buyer's guild costs 10
    assert await gold_of(db, yours) == 200 - 130
    clock.move(2 * DAY)
    assert await service.expire_due(db, ECONOMY, STORAGE) == 1
    await db.refresh(listing)
    assert (listing.state, listing.buyer_team_id) == (SOLD, yours.id)
    assert await gold_of(db, mine) == 95 + 120 and await gold_of(db, yours) == 70  # the fee went to the guild
    assert (await db.get(Escrow, listing.bid_escrow_id)).state == RELEASED
    [parcel] = await delivery.parcels(db, to_team_id=yours.id)
    assert (parcel.guild, parcel.purpose, parcel.ref) == ("rearguard", "purchase", str(listing.id))
    clock.move(3600)
    await delivery.collect(db, ECONOMY, STORAGE, cole, parcel.id)
    assert await holding(db, cole, "potion") == 2
    assert await service.expire_due(db, ECONOMY, STORAGE) == 0  # settled once
