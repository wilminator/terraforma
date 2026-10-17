"""Barter: a listing that asks for items, paid in items that go to the seller as a parcel, on every database."""

import pytest

from terraforma.delivery import service as delivery
from terraforma.escrow.models import RELEASED, Escrow
from terraforma.marketplace import service
from terraforma.marketplace.hooks import Marketplace
from terraforma.marketplace.models import SOLD

from .test_marketplace import ECONOMY, FEES, STORAGE, a_market, clock, gold_of  # noqa: F401
from .test_trading import holding, stock

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

HOOKS = Marketplace()


async def swap_listing(db, aria, gold=0, asks=(("sword", 1),), items=((0, 2),)):
    return await service.post(db, ECONOMY, FEES, HOOKS, aria, "vanguard", list(items), gold, 0, list(asks))


async def test_a_listing_can_ask_only_for_items_and_search_shows_them(clock, db):
    _mike, _co, mine, _yours, aria, _cole = await a_market(db)
    listing = await swap_listing(db, aria)
    assert (listing.price_gold, listing.price_tokens) == (0, 0)
    assert await service.asks_of(db, listing) == [("sword", 1)]
    [row] = await service.search(db, "vanguard")
    assert row["asks"] == [("sword", 1)] and row["price_gold"] == 0
    assert await gold_of(db, mine) == 100  # a barter with no gold price has no posting fee by default


async def test_the_buyer_pays_in_items_that_reach_the_seller_as_a_parcel(clock, db):
    _mike, _co, mine, yours, aria, cole = await a_market(db)
    await stock(db, cole, "sword")
    listing = await swap_listing(db, aria, gold=30)
    parcel = await service.buy(db, ECONOMY, FEES, HOOKS, cole, listing.id, "vanguard", [(0, 1)])
    assert parcel.to_team_id == yours.id and await holding(db, cole, "sword") == 0
    assert await gold_of(db, yours) == 170 and await gold_of(db, mine) == 100 - 1 + 30
    [paid] = await delivery.parcels(db, to_team_id=mine.id)
    assert (paid.purpose, paid.ref, paid.guild) == ("barter", str(listing.id), "vanguard")
    assert (await delivery.contents(db, paid))["items"] == [("sword", 1)]
    await delivery.collect(db, ECONOMY, STORAGE, aria, paid.id)
    assert await holding(db, aria, "sword") == 2  # her own sword and the buyer's
    await db.refresh(listing)
    assert listing.state == SOLD


async def test_the_wrong_payment_changes_nothing(clock, db):
    _mike, _co, mine, yours, aria, cole = await a_market(db)
    await stock(db, cole, "sword", 2)
    await stock(db, cole, "potion", 1)
    listing = await swap_listing(db, aria)
    listing_id = listing.id  # a refused buy expires the objects: keep the id
    for pay, match in (
        (None, "say which of yours"), ([(0, 1), (1, 1)], "not the items asked"), ([(2, 1)], "not the items asked"),  # two swords; a potion
        ([(9, 1)], "nothing in that position"),
    ):
        with pytest.raises(service.MarketplaceError, match=match):
            await service.buy(db, ECONOMY, FEES, HOOKS, cole, listing_id, "vanguard", pay)
    assert await holding(db, cole, "sword") == 2 and await holding(db, cole, "potion") == 1
    assert await gold_of(db, yours) == 200
    await db.refresh(listing)
    assert listing.state == "open"
    await service.buy(db, ECONOMY, FEES, HOOKS, cole, listing_id, "vanguard", [(0, 1)])  # the one sword asked for is accepted
    plain = await service.post(db, ECONOMY, FEES, HOOKS, aria, "vanguard", [(0, 1)], 10)
    with pytest.raises(service.MarketplaceError, match="asks for no items"):
        await service.buy(db, ECONOMY, FEES, HOOKS, cole, plain.id, "vanguard", [(0, 1)])


async def test_asks_must_be_real_items_named_once(clock, db):
    _mike, _co, _mine, _yours, aria, _cole = await a_market(db)
    for asks, match in (
        ([("nonesuch", 1)], "no item"), ([("sword", 0)], "at least 1"), ([("sword", 1), ("sword", 2)], "named once"),
        ([(f"i{n}", 1) for n in range(9)], "at most"),
    ):
        with pytest.raises(service.MarketplaceError, match=match):
            await swap_listing(db, aria, asks=asks)
