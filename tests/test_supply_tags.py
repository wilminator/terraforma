"""The dialog tags that read and change a settlement's supply of a material: ``supply_level``, ``supply_add`` and ``supply_take``."""

import pytest

from terraforma.models import Map, advance_clock
from terraforma.npcs.script import ScriptError, parse
from terraforma.npcs.service import NpcError
from terraforma.realms import service as realms
from terraforma.realms import supply
from terraforma.realms.models import Material, SettlementSupply

from .test_guild_ranks import says
from .test_npc_state import a_party

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


async def a_town(db, hero, level=40, capacity=100, drain=0):
    """A live settlement 'town' on the hero's map keeping $level of $capacity herbs; returns its row."""
    world_id = (await db.get(Map, hero.map_id)).world_id
    realm = await realms.create_realm(db, world_id, "ardent", "Ardent")
    town = await realms.add_settlement(db, realm, hero.map_id, "town", "Town")
    herbs = Material(key="herbs", name="Herbs")
    db.add(herbs)
    await db.flush()
    row = SettlementSupply(settlement_id=town.id, material_id=herbs.id, level=level, capacity=capacity, drain=drain, as_of_tick=0)
    db.add(row)
    await db.flush()
    return town, row


def test_the_supply_tags_are_checked_when_the_text_is_placed():
    parse("`supply_level,town,herbs,lt,50,no``supply_add,town,herbs,10,no``supply_take,town,herbs,5,no``label,no`")
    for bad in (
        "`supply_level,town,herbs,lt,50`",  # too few parameters
        "`supply_level,town,herbs,below,50,end`",  # not a comparison
        "`supply_level,town,herbs,lt,101,end`",  # more than 100 percent
        "`supply_level,town,herbs,lt,half,end`",  # not a number
        "`supply_add,town,herbs,0,end`",  # nothing to bring
        "`supply_take,town,herbs,-1,end`",  # not a whole number
        "`supply_add,town,herbs,5`",  # too few parameters
        "`supply_take,town,herbs,5,nowhere`",  # label never defined
        "`supply_add,,herbs,5,end`",  # no settlement
    ):
        with pytest.raises(ScriptError):
            parse(bad)


async def test_supply_level_offers_a_quest_only_while_the_supply_is_low(db):
    hero = (await a_party(db)).heroes[0]
    town, row = await a_town(db, hero, level=40)
    board = "`supply_level,town,herbs,lt,50,full`Herbs are running short: bring some.`end``label,full`All is well."
    assert await says(db, hero, board) == "Herbs are running short: bring some."
    row.level = 50
    await db.flush()
    assert await says(db, hero, board) == "All is well.", "half full is not low"


async def test_supply_level_follows_the_world_clock(db):
    hero = (await a_party(db)).heroes[0]
    town, row = await a_town(db, hero, level=60, drain=2)
    board = "`supply_level,town,herbs,lt,30,fine`Short.`end``label,fine`Fine."
    assert await says(db, hero, board) == "Fine."
    await advance_clock(db, town.world_id, 20)  # 60 - 40 = 20 of 100
    assert await says(db, hero, board) == "Short."


async def test_supply_add_brings_goods_in_up_to_the_capacity_and_takes_nothing_from_the_hero(db):
    hero = (await a_party(db)).heroes[0]
    town, row = await a_town(db, hero, level=95)
    deliver = "`supply_add,town,herbs,10,full`Delivered.`end``label,full`The store is full."
    assert await says(db, hero, deliver) == "Delivered."
    assert (await supply.supply_of(db, town, "herbs")).level == 100, "only the 5 that fit"
    assert await says(db, hero, deliver) == "The store is full."
    assert (await supply.supply_of(db, town, "herbs")).level == 100


async def test_supply_take_is_all_or_nothing(db):
    hero = (await a_party(db)).heroes[0]
    town, row = await a_town(db, hero, level=8)
    craft = "`supply_take,town,herbs,5,short`Crafted.`end``label,short`Not enough herbs."
    assert await says(db, hero, craft) == "Crafted."
    assert (await supply.supply_of(db, town, "herbs")).level == 3
    assert await says(db, hero, craft) == "Not enough herbs."
    assert (await supply.supply_of(db, town, "herbs")).level == 3, "nothing is taken when there is too little"


async def test_a_dialog_naming_a_supply_the_world_does_not_have_says_so(db):
    hero = (await a_party(db)).heroes[0]
    await a_town(db, hero)
    for text, message in (
        ("`supply_level,town,iron,lt,50,end`", "no supply of 'iron' at 'town'"),
        ("`supply_add,nowhere,herbs,5,end`", "no supply of 'herbs' at 'nowhere'"),
        ("`supply_take,town,iron,5,end`", "no supply of 'iron'"),
    ):
        with pytest.raises(NpcError, match=message):  # (the dialog service reports the game's mistake as an NpcError, and undoes the step)
            await says(db, hero, text)


# --- the seed check -----------------------------------------------------------------------------------------------------

def test_a_dialog_that_names_a_supply_the_seed_has_checks_and_one_that_does_not_is_refused_wherever_it_stands():
    import copy

    from terraforma.content.schema import ContentError, check_seed

    from .test_realm_supply import with_supply

    def seed(text: str, where: str):
        data = copy.deepcopy(with_supply())  # settlement 'capital' keeps iron and hide
        if where == "dialogs":
            data["dialogs"] = [{"key": "board", "text": text}]
        elif where == "npcs":
            data["npcs"] = [{"key": "clerk", "name": "Clerk", "map": "hub", "x": 1, "y": 1, "dialog": text}]
        else:
            data["maps"][0]["objects"] = [{"key": "crate", "name": "Crate", "x": 0, "y": 0, "script": text}]
        return data

    for where in ("dialogs", "npcs", "maps"):
        check_seed(seed("`supply_level,capital,iron,lt,50,no``supply_add,capital,hide,5,no``supply_take,capital,iron,2,no``label,no`", where))
        with pytest.raises(ContentError, match="names the settlement 'nowhere', which settlements.json doesn't have"):
            check_seed(seed("`supply_add,nowhere,iron,5,no``label,no`", where))
        with pytest.raises(ContentError, match="names 'gold' at 'capital', which that settlement doesn't keep"):
            check_seed(seed("`supply_take,capital,gold,5,no``label,no`", where))
