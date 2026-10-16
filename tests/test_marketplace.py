"""The marketplace: listing goods at a guild, finding them, buying them (delivered, never handed over), taking them back, on every database."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.challenge import service as challenge
from terraforma.delivery import service as delivery
from terraforma.delivery.models import TRANSIT
from terraforma.economy import TeamGold
from terraforma.escrow.models import HELD, RELEASED, Escrow
from terraforma.guild.fees import Fees, RankFees
from terraforma.guild import ranks
from terraforma.heroes import inventory
from terraforma.marketplace import service
from terraforma.marketplace.hooks import Marketplace, Route
from terraforma.marketplace.models import CANCELLED, EXPIRED, OPEN, SOLD, Listing
from terraforma.storage.hooks import NoStorage

from .test_trading import account, holding, stock, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
FEES = Fees()
STORAGE = NoStorage()
DAY = 24 * 3600


@pytest.fixture
def clock(monkeypatch):
    """The wall clock, moved by `clock.move(seconds)`."""
    now = [wallclock.now()]
    monkeypatch.setattr(wallclock, "now", lambda: now[0])

    class Clock:
        @staticmethod
        def move(seconds):
            now[0] += timedelta(seconds=seconds)

    return Clock


async def a_market(db):
    """A seller (Aria, 100 gold, two potions and a sword, on Vanguard) and a buyer (Cole, 200 gold, on Rearguard), owned by two players."""
    mike = await account(db)
    cole_owner = await account(db, "Cole")
    mine, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    yours, (cole,) = await team_with(db, cole_owner, "Rearguard", ["Cole"])
    await ECONOMY.credit_team(db, mine.id, 100)
    await ECONOMY.credit_team(db, yours.id, 200)
    await stock(db, aria, "potion", 2)
    await stock(db, aria, "sword")
    return mike, cole_owner, mine, yours, aria, cole


async def gold_of(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


async def listed(db, aria, guild="vanguard", items=((0, 2),), gold=100, tokens=0, hooks=None):
    return await service.post(db, ECONOMY, FEES, hooks or Marketplace(), aria, guild, list(items), gold, tokens)


# --- posting -----------------------------------------------------------------------------------------------------------

async def test_posting_takes_the_goods_and_the_fee_and_opens_a_listing_for_a_week(clock, db):
    _mike, _cole_owner, mine, _yours, aria, _cole = await a_market(db)
    listing = await listed(db, aria)
    assert (listing.state, listing.guild, listing.seller_team_id, listing.price_gold) == (OPEN, "vanguard", mine.id, 100)
    assert listing.expires_at == wallclock.timestamp() + 7 * DAY
    assert await holding(db, aria, "potion") == 0 and await holding(db, aria, "sword") == 1
    assert await gold_of(db, mine) == 95  # the fee: a twentieth of 100
    assert await service.goods(db, listing) == [("potion", 2)]


async def test_a_posting_that_cannot_be_made_changes_nothing(clock, db):
    _mike, _cole_owner, mine, _yours, aria, _cole = await a_market(db)
    for items, gold, tokens, match in (([], 10, 0, "nothing to list"), ([(0, 1)], 0, 0, "some gold or tokens"), ([(0, 1)], -1, 0, "some gold or tokens"),
                                      ([(0, 9)], 10, 0, "you have 2"), ([(7, 1)], 10, 0, "nothing in that position")):
        with pytest.raises(service.MarketplaceError, match=match):
            await service.post(db, ECONOMY, FEES, Marketplace(), aria, "vanguard", items, gold, tokens)
    await ECONOMY.debit(db, aria, 100)  # now the fee cannot be paid
    with pytest.raises(service.MarketplaceError, match="not enough gold"):
        await listed(db, aria)
    assert await db.scalar(select(func.count()).select_from(Listing)) == 0
    assert await db.scalar(select(func.count()).select_from(Escrow)) == 0
    assert await holding(db, aria, "potion") == 2


async def test_the_posting_fee_follows_the_guilds_fee_rule(clock, db):
    mike, _cole_owner, mine, _yours, aria, _cole = await a_market(db)
    await ranks.set_rank(db, mine.id, "merchants", 3)
    rule = RankFees(rank_discounts=(0, 10, 20, 50))
    await service.post(db, ECONOMY, rule, Marketplace(), aria, "merchants", [(0, 1)], 200)  # base fee 10, half off at rank 3
    assert await gold_of(db, mine) == 95


# --- buying ------------------------------------------------------------------------------------------------------------

async def test_a_purchase_pays_the_seller_and_delivers_the_goods_as_a_parcel(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    listing = await listed(db, aria)
    parcel = await service.buy(db, ECONOMY, FEES, Marketplace(), cole, listing.id, "vanguard")
    assert (parcel.to_team_id, parcel.guild, parcel.purpose, parcel.ref, parcel.state) == (yours.id, "vanguard", "purchase", str(listing.id), TRANSIT)
    assert await gold_of(db, yours) == 100 and await gold_of(db, mine) == 195  # paid 100; the seller got it after the 5 fee
    assert await holding(db, cole, "potion") == 0  # delivered, not handed over
    await db.refresh(listing)
    assert (listing.state, listing.buyer_team_id) == (SOLD, yours.id)
    assert (await db.get(Escrow, listing.escrow_id)).state == RELEASED
    await delivery.collect(db, ECONOMY, STORAGE, cole, parcel.id)
    assert await holding(db, cole, "potion") == 2


async def test_a_purchase_for_another_guild_pays_the_route_and_waits_for_it(clock, db):
    _mike, _cole_owner, _mine, yours, aria, cole = await a_market(db)
    listing = await listed(db, aria)
    parcel = await service.buy(db, ECONOMY, FEES, Marketplace(), cole, listing.id, "rearguard")
    assert await gold_of(db, yours) == 90  # 100 for the goods and 10 for the road
    assert parcel.arrives_at == wallclock.timestamp() + 3600
    with pytest.raises(delivery.DeliveryError, match="not arrived|no such parcel|on its way"):
        await delivery.collect(db, ECONOMY, STORAGE, cole, parcel.id)
    clock.move(3600)
    await delivery.collect(db, ECONOMY, STORAGE, cole, parcel.id)
    assert await holding(db, cole, "potion") == 2


async def test_the_delivery_fee_follows_the_fee_rule_and_the_games_route(clock, db):
    _mike, _cole_owner, _mine, yours, aria, cole = await a_market(db)
    await ranks.set_rank(db, yours.id, "rearguard", 1)

    class Bazaar(Marketplace):
        def delivery(self, from_guild, to_guild):
            return Route(fee=40, delay=60)

    listing = await listed(db, aria)
    parcel = await service.buy(db, ECONOMY, RankFees(rank_discounts=(0, 25)), Bazaar(), cole, listing.id, "rearguard")
    assert await gold_of(db, yours) == 70  # 100 for the goods and 30 for the road
    assert parcel.arrives_at == wallclock.timestamp() + 60


async def test_a_token_price_moves_tokens_between_the_players(clock, db):
    mike, cole_owner, _mine, _yours, aria, cole = await a_market(db)
    await challenge.change(db, cole_owner.id, 10, "test")
    listing = await listed(db, aria, gold=0, tokens=4)
    await service.buy(db, ECONOMY, FEES, Marketplace(), cole, listing.id, "vanguard")
    assert await challenge.balance(db, cole_owner.id) == 6 and await challenge.balance(db, mike.id) == 4
    assert await challenge.audit(db, cole_owner.id) and await challenge.audit(db, mike.id)


async def test_a_purchase_that_cannot_be_made_leaves_the_listing_open(clock, db):
    mike, cole_owner, mine, yours, aria, cole = await a_market(db)
    listing = await listed(db, aria, gold=150, tokens=5)
    listing_id = listing.id  # a refused purchase expires what the session holds
    with pytest.raises(service.MarketplaceError, match="not enough tokens"):  # the gold was paid before, and comes back
        await service.buy(db, ECONOMY, FEES, Marketplace(), cole, listing_id, "vanguard")
    with pytest.raises(service.MarketplaceError, match="own listing"):
        await service.buy(db, ECONOMY, FEES, Marketplace(), aria, listing_id, "vanguard")
    with pytest.raises(service.MarketplaceError, match="gone"):
        await service.buy(db, ECONOMY, FEES, Marketplace(), cole, 9999, "vanguard")
    await db.refresh(listing)
    assert listing.state == OPEN and await gold_of(db, yours) == 200
    assert (await db.get(Escrow, listing.escrow_id)).state == HELD
    await challenge.change(db, cole_owner.id, 5, "test")
    await ECONOMY.debit(db, cole, 100)  # now the gold is short
    with pytest.raises(service.MarketplaceError, match="not enough gold"):
        await service.buy(db, ECONOMY, FEES, Marketplace(), cole, listing_id, "vanguard")
    assert await challenge.balance(db, cole_owner.id) == 5
    await db.refresh(listing)
    assert listing.state == OPEN


async def test_a_listing_is_sold_once_and_not_after_it_closes(clock, db):
    _mike, _cole_owner, _mine, yours, aria, cole = await a_market(db)
    first = await listed(db, aria)
    await service.buy(db, ECONOMY, FEES, Marketplace(), cole, first.id, "vanguard")
    with pytest.raises(service.MarketplaceError, match="gone"):
        await service.buy(db, ECONOMY, FEES, Marketplace(), cole, first.id, "vanguard")
    second = await listed(db, aria, items=((0, 1),), gold=10)
    clock.move(7 * DAY)
    with pytest.raises(service.MarketplaceError, match="gone"):
        await service.buy(db, ECONOMY, FEES, Marketplace(), cole, second.id, "vanguard")
    assert await gold_of(db, yours) == 100


# --- taking back -------------------------------------------------------------------------------------------------------

async def test_the_seller_can_cancel_and_gets_the_goods_back_without_the_fee(clock, db):
    _mike, _cole_owner, mine, _yours, aria, cole = await a_market(db)
    listing = await listed(db, aria)
    with pytest.raises(service.MarketplaceError, match="no such listing of yours"):
        await service.cancel(db, ECONOMY, STORAGE, cole, listing.id)
    done = await service.cancel(db, ECONOMY, STORAGE, aria, listing.id)
    assert done.state == CANCELLED and await holding(db, aria, "potion") == 2 and await gold_of(db, mine) == 95
    with pytest.raises(service.MarketplaceError, match="settled already"):
        await service.cancel(db, ECONOMY, STORAGE, aria, listing.id)


async def test_a_listing_that_runs_out_of_time_sends_the_goods_back(clock, db):
    _mike, _cole_owner, _mine, _yours, aria, _cole = await a_market(db)
    listing = await listed(db, aria)
    assert await service.expire_due(db, ECONOMY, STORAGE) == 0
    clock.move(7 * DAY)
    assert await service.expire_due(db, ECONOMY, STORAGE) == 1
    await db.refresh(listing)
    assert listing.state == EXPIRED and await holding(db, aria, "potion") == 2
    assert await service.expire_due(db, ECONOMY, STORAGE) == 0


# --- finding -----------------------------------------------------------------------------------------------------------

async def test_search_finds_open_listings_by_guild_text_and_price(clock, db):
    _mike, _cole_owner, _mine, _yours, aria, cole = await a_market(db)
    potions = await listed(db, aria, guild="vanguard", items=((0, 2),), gold=50)
    sword = await listed(db, aria, guild="merchants", items=((0, 1),), gold=300)
    ids = lambda found: [row["id"] for row in found]  # noqa: E731
    assert ids(await service.search(db)) == [potions.id, sword.id]  # cheapest first
    assert ids(await service.search(db, guild="merchants")) == [sword.id]
    assert ids(await service.search(db, text="POTION")) == [potions.id]
    assert ids(await service.search(db, text="swo")) == [sword.id]
    assert ids(await service.search(db, max_gold=100)) == [potions.id]
    assert ids(await service.search(db, text="nothing like it")) == []
    first = (await service.search(db, text="potion"))[0]
    assert (first["seller"], first["guild"], first["price_gold"], first["goods"]) == ("Vanguard", "vanguard", 50, [("potion", 2)])
    assert ids(await service.search(db, limit=1, offset=1)) == [sword.id]


async def test_search_leaves_out_what_is_sold_cancelled_or_out_of_time(clock, db):
    _mike, _cole_owner, _mine, _yours, aria, cole = await a_market(db)
    sold = await listed(db, aria, items=((0, 1),), gold=10)
    await service.buy(db, ECONOMY, FEES, Marketplace(), cole, sold.id, "vanguard")
    gone = await listed(db, aria, items=((0, 1),), gold=20)
    await service.cancel(db, ECONOMY, STORAGE, aria, gone.id)
    old = await listed(db, aria, items=((0, 1),), gold=30)
    assert [row["id"] for row in await service.search(db)] == [old.id]
    clock.move(7 * DAY)
    assert await service.search(db) == []
