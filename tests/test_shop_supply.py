"""A shop's items tied to a settlement's supply: dearer as the supply runs low, gone when it runs out, and nothing changes for an item that is not tied."""

import pytest

from terraforma.market import service as market
from terraforma.market.hooks import Supplied
from terraforma.models import Map, advance_clock
from terraforma.realms import service as realms
from terraforma.realms.models import Material, SettlementSupply

from .test_market import ECONOMY, RULES, SHOP, Flat, a_hero, buy, sell, talking

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


class Smithy(Flat):
    """Herbs depend on what the town keeps of the herb material; nothing else does."""

    async def supplied(self, session, shop, item):
        return Supplied("town", "herbs") if item.key == "herb" else None


class Thrifty(Smithy):
    """A game's own curve: full price down to a quarter full, then 300%, and sold out when empty."""

    def price_percent(self, shop, item, fullness):
        return None if fullness == 0 else (100 if fullness >= 25 else 300)


async def a_town(db, hero, level=100, capacity=100, drain=0):
    """A live settlement on the hero's map that keeps $level of $capacity herbs; returns the world's id."""
    world_id = (await db.get(Map, hero.map_id)).world_id
    realm = await realms.create_realm(db, world_id, "ardent", "Ardent")
    town = await realms.add_settlement(db, realm, hero.map_id, "town", "Town")
    herbs = Material(key="herbs", name="Herbs")
    db.add(herbs)
    await db.flush()
    db.add(SettlementSupply(settlement_id=town.id, material_id=herbs.id, level=level, capacity=capacity, drain=drain, as_of_tick=0))
    await db.flush()
    return world_id


async def prices(db, hero, rules=None):
    """The shop's wares as {item: price}."""
    prompt = await talking(db, hero, SHOP)
    seen = await market.view(db, rules or Smithy(), RULES, ECONOMY, hero, prompt)
    return {ware["item"]: ware["price"] for ware in seen["wares"]}, prompt


async def test_an_item_not_tied_to_a_supply_sells_at_its_own_price_whatever_the_supply_is(db):
    hero, team = await a_hero(db)
    await a_town(db, hero, level=0)
    found, _prompt = await prices(db, hero, Flat())
    assert found == {"herb": 10, "potion": 25, "sword": 100}, "the base Market ties nothing"
    found, _prompt = await prices(db, hero)
    assert found["potion"] == 25 and found["sword"] == 100, "only the tied item changes"


async def test_a_well_stocked_supply_sells_at_the_normal_price_and_a_low_one_costs_more(db):
    hero, team = await a_hero(db)
    await a_town(db, hero, level=100)
    assert (await prices(db, hero))[0]["herb"] == 10
    for level, expected in ((50, 10), (30, 15), (10, 20)):  # the default line: 100% from half full, 200% at a tenth
        row = await db.get(SettlementSupply, 1)
        row.level = level
        await db.flush()
        assert (await prices(db, hero))[0]["herb"] == expected, f"{level} of 100"


async def test_the_item_sells_out_below_the_threshold_and_cannot_be_bought(db):
    hero, team = await a_hero(db)
    await a_town(db, hero, level=9)
    found, prompt = await prices(db, hero)
    assert "herb" not in found and "potion" in found
    with pytest.raises(market.ShopError, match="isn't for sale"):
        await buy(db, hero, prompt, "herb", rules=Smithy())


async def test_the_world_clock_moves_the_price(db):
    hero, team = await a_hero(db)
    world_id = await a_town(db, hero, level=50, drain=2)
    assert (await prices(db, hero))[0]["herb"] == 10
    await advance_clock(db, world_id, 10)  # 50 - 2 * 10 = 30
    assert (await prices(db, hero))[0]["herb"] == 15
    await advance_clock(db, world_id, 100)  # drained to nothing
    assert "herb" not in (await prices(db, hero))[0]


async def test_a_supply_that_does_not_exist_counts_as_empty(db):
    hero, team = await a_hero(db)
    await a_town(db, hero)

    class Typo(Flat):
        async def supplied(self, session, shop, item):
            return Supplied("town", "no-such-material") if item.key == "herb" else None

    assert "herb" not in (await prices(db, hero, Typo()))[0]

    class Nowhere(Flat):
        async def supplied(self, session, shop, item):
            return Supplied("no-such-town", "herbs") if item.key == "herb" else None

    assert "herb" not in (await prices(db, hero, Nowhere()))[0]


async def test_a_game_sets_its_own_curve_and_thresholds(db):
    hero, team = await a_hero(db)
    await a_town(db, hero, level=20)
    found, prompt = await prices(db, hero, Thrifty())
    assert found["herb"] == 30
    done = await buy(db, hero, prompt, "herb", 2, rules=Thrifty())
    assert done["cost"] == 60, "the dear price is what is charged"
    row = await db.get(SettlementSupply, 1)
    row.level = 0
    await db.flush()
    assert "herb" not in (await prices(db, hero, Thrifty()))[0]


async def test_what_the_shop_pays_back_does_not_follow_the_supply(db):
    hero, team = await a_hero(db)
    await a_town(db, hero, level=30)  # herbs cost 15 here now (150%)
    found, prompt = await prices(db, hero)
    assert found["herb"] == 15
    await market.buy(db, Smithy(), RULES, ECONOMY, hero, prompt, "herb", 1)
    seen = await market.view(db, Smithy(), RULES, ECONOMY, hero, prompt)
    assert seen["sellable"][0]["each"] == 8, "75% of the item's own price (10), rounded half up"
    assert (await sell(db, hero, prompt, seen["sellable"][0]["position"], rules=Smithy()))["paid"] == 8
