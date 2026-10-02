"""Item drops: the tables, the seed format, who gets what, and what is saved. Pure rules need no database."""

import copy
import json

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.economy import TeamGold
from terraforma.fights import drops, live, status, store
from terraforma.fights.build import known_drop_tables
from terraforma.fights.combatant import Command
from terraforma.fights.drops import DropEntry, DropTable, roll_table
from terraforma.fights.events import Event, EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.replay import apply_events
from terraforma.fights.rules import Rules
from terraforma.fights.specs import ItemSpec
from terraforma.fights.state import dehydrate, hydrate
from terraforma.heroes import inventory
from terraforma.content.models import Item
from terraforma.heroes.models import Hero, HeroItem
from terraforma.models import Account

from .test_fight_rules import RULES, Scripted, fighter, listing
from .test_fight_store import SEED, a_hero

pytestmark = pytest.mark.anyio

POTION = ItemSpec("potion", "Potion", one_use=True)
SWORD = ItemSpec("sword", "Sword", equip_slots=("hand",))
ARROW = ItemSpec("arrow", "Arrow", equip_slots=("ammo",))


def plain(*entries, **changes):
    return DropTable("spoils", "Spoils", entries=tuple(entries), **changes)


# --- rolling a table ------------------------------------------------------------------------------------------------

def test_a_plain_table_rolls_every_entry_on_its_own_chance_out_of_the_scale():
    table = plain(DropEntry(POTION, chance=5000), DropEntry(SWORD, chance=1), DropEntry(ARROW, chance=10000))
    found = roll_table(10000, table, Scripted(5000, 2, 7))
    assert [roll.item.key for roll in found] == ["potion", "arrow"], "5000 is within 5000, 2 is not within 1, and a sure thing is sure"
    assert [roll.item.key for roll in roll_table(10000, table, Scripted(5001, 1, 10000))] == ["sword", "arrow"]


def test_a_quantity_range_draws_a_number_and_a_fixed_one_draws_nothing():
    table = plain(DropEntry(POTION, chance=10000, minimum=2, maximum=5), DropEntry(ARROW, chance=10000, minimum=3, maximum=3))
    found = roll_table(10000, table, Scripted(1, 4, 1))  # chance, how many, chance (no draw for how many: it is always 3)
    assert [(roll.item.key, roll.quantity) for roll in found] == [("potion", 4), ("arrow", 3)]


def test_a_game_can_set_its_own_scale():
    table = plain(DropEntry(POTION, chance=50))
    assert roll_table(100, table, Scripted(50)) and not roll_table(100, table, Scripted(51))


def test_a_weighted_table_picks_by_weight_several_times_and_can_drop_nothing():
    table = DropTable("w", "W", weighted=True, rolls=3, entries=(DropEntry(POTION, weight=2), DropEntry(None, weight=5), DropEntry(SWORD, weight=3)))
    found = roll_table(10000, table, Scripted(1, 2, 8))  # potion (1-2), potion again, sword (8-10)
    assert [roll.item.key for roll in found] == ["potion", "potion", "sword"]
    assert [roll.item.key for roll in roll_table(10000, table, Scripted(1, 3, 10))] == ["potion", "sword"], "3 to 7 is nothing"
    assert roll_table(10000, DropTable("e", "E", weighted=True, entries=()), Scripted()) == [], "nothing to pick from"


# --- what a fighter can carry -----------------------------------------------------------------------------------------------

def test_dropped_items_stack_to_the_stack_size_gear_never_stacks_and_what_does_not_fit_is_said():
    hero = fighter("Hero")
    assert hero.add_item(RULES, POTION, 300) == 0 and [qty for _item, qty in hero.inventory] == [250, 50]
    assert hero.add_item(RULES, POTION, 210) == 0 and [qty for _item, qty in hero.inventory] == [250, 250, 10]
    assert hero.add_item(RULES, SWORD, 2) == 0 and [item.key for item, _qty in hero.inventory][-2:] == ["sword", "sword"]
    assert hero.add_item(RULES, ARROW, 255) == 0 and len(hero.inventory) == 7, "ammunition stacks"
    for _ in range(5):
        hero.add_item(RULES, SWORD, 1)
    assert len(hero.inventory) == 12
    assert hero.add_item(RULES, SWORD, 3) == 3 and hero.add_item(RULES, POTION, 300) == 60, "240 more fit in the last potion stack, the rest do not"


def test_the_rules_keep_to_the_inventorys_own_limits():
    assert (Rules.inventory_stacks, Rules.stack_size) == (inventory.MAX_ITEMS, inventory.MAX_ITEM_QTY)


# --- who gets it ------------------------------------------------------------------------------------------------------------------

def hero_fight(drop_tables=(), area=(), monsters=1, heroes=3, teams=((10, (0, 1)), (11, (2,)))):
    party = [fighter(f"Hero{n}") for n in range(heroes)]
    for number, hero in enumerate(party):
        hero.charid = 100 + number
    foes = [fighter(f"Foe{n}", HP=10) for n in range(monsters)]
    for foe in foes:
        foe.drops = tuple(table.key for table in drop_tables)
    fight = build_fight({0: {0: party}, 1: {0: foes}}, drop_tables={table.key: table for table in drop_tables}, area_drops=list(area))
    fight.parties[0].teams = {team: [100 + member for member in members] for team, members in teams}
    return fight


def kill(fight, address):
    fight.get(address).current["HP"] = 0


def harmed_by(fight, monster, *heroes, ratio=0.5):
    for hero in heroes:
        fight.get(monster).xp_debts.append([0, 0, hero, ratio, 10])


def test_contributors_are_those_who_harmed_it_and_those_who_buffed_a_harmer_and_never_healers():
    fight = hero_fight()
    harmed_by(fight, (1, 0, 0), 0)
    fight.get((1, 0, 0)).xp_debts.append([0, 0, 1, -0.5, 10])  # a heal (a negative debt) does not count
    status.place(fight.get((0, 0, 0)), status.StatusSpec("haste", "Haste", status.GOOD), (0, 0, 2), 3)
    status.place(fight.get((0, 0, 1)), status.StatusSpec("curse", "Curse", status.BAD), (0, 0, 2), 3)  # a bad status is not a buff
    assert fight.get((0, 0, 0)).buffed_by == [(0, 0, 2)] and fight.get((0, 0, 1)).buffed_by == []
    assert drops.contributors(fight, [(1, 0, 0)]) == {(0, 0, 0), (0, 0, 2)}


def test_one_drop_goes_to_one_random_contributor_or_to_anyone_when_none_contributed():
    fight = hero_fight()
    harmed_by(fight, (1, 0, 0), 1, 2)
    assert drops.default_recipients(fight, 0, [(1, 0, 0)], "one", Scripted()) == [(0, 0, 1)], "the first of the pool, with the scripted choice"
    assert drops.default_recipients(hero_fight(), 0, [(1, 0, 0)], "one", Scripted()) == [(0, 0, 0)], "no contributors: all the heroes are in the pool"


def test_a_drop_for_each_team_goes_to_one_in_each_and_for_each_member_to_everyone():
    fight = hero_fight()
    harmed_by(fight, (1, 0, 0), 1)
    assert drops.default_recipients(fight, 0, [(1, 0, 0)], "each_team", Scripted()) == [(0, 0, 1), (0, 0, 2)], "a contributor in the first team, anyone in the second"
    assert drops.default_recipients(fight, 0, [(1, 0, 0)], "each_member", Scripted()) == [(0, 0, 0), (0, 0, 1), (0, 0, 2)]
    assert drops.default_recipients(fight, 1, [(0, 0, 0)], "one", Scripted()) == [], "monsters are nobody's heroes"


# --- the end of a fight -----------------------------------------------------------------------------------------------------------

def test_a_won_fight_rolls_the_monsters_tables_and_the_drops_are_events_that_replay():
    table = plain(DropEntry(POTION, chance=10000, minimum=2, maximum=2))
    fight = hero_fight([table], monsters=2)
    for monster in ((1, 0, 0), (1, 0, 1)):
        harmed_by(fight, monster, 0)
        kill(fight, monster)
    before = copy.deepcopy(fight)
    events = RULES.roll_drops(fight, Scripted(1, 1))
    assert listing(events) == [("Drop", 0, 0, 0, "potion", 2)] * 2
    assert fight.get((0, 0, 0)).inventory == [[POTION, 4]]
    apply_events(before, RULES, [Event.from_list(json.loads(json.dumps(each.to_list()))) for each in events])
    assert dehydrate(before) == dehydrate(fight), "the log says what dropped"


def test_a_party_that_lost_gets_nothing_and_a_monster_still_standing_drops_nothing():
    table = plain(DropEntry(POTION, chance=10000))
    fight = hero_fight([table], monsters=2)
    kill(fight, (1, 0, 0))
    assert [each.type for each in RULES.roll_drops(fight, Scripted(1))] == [EventType.DROP], "only the dead one"
    lost = hero_fight([table])
    for hero in range(3):
        kill(lost, (0, 0, hero))
    kill(lost, (1, 0, 0))
    assert RULES.roll_drops(lost, Scripted()) == [], "nobody of the party is standing"


def test_the_areas_tables_roll_once_for_the_fight_or_for_every_monster_by_the_rule():
    area = DropTable("area", "Area", entries=(DropEntry(ARROW, chance=10000),))
    fight = hero_fight(area=["area"], monsters=3)
    fight.drop_tables["area"] = area
    for monster in range(3):
        kill(fight, (1, 0, monster))
    assert len(RULES.roll_drops(copy.deepcopy(fight), Scripted(1))) == 1, "once, by default"

    class PerMonster(Rules):
        map_drops_once_per_fight = False

    assert len(PerMonster().roll_drops(copy.deepcopy(fight), Scripted(1, 1, 1))) == 3


def test_what_does_not_fit_is_a_drop_lost_event_and_the_game_can_choose_who_gets_it():
    table = plain(DropEntry(SWORD, chance=10000, minimum=4, maximum=4, share="each_member"))
    fight = hero_fight([table], heroes=2, teams=((10, (0, 1)),))
    for _ in range(10):
        fight.get((0, 0, 0)).add_item(RULES, SWORD, 1)
    kill(fight, (1, 0, 0))
    events = listing(RULES.roll_drops(fight, Scripted(1)))
    assert events == [("Drop", 0, 0, 0, "sword", 2), ("DropLost", 0, 0, 0, "sword", 2), ("Drop", 0, 0, 1, "sword", 4)]

    class Leader(Rules):
        def drop_recipients(self, fight, party, monsters, share, rng):
            return [(party, 0, 1)]

    fight = hero_fight([plain(DropEntry(POTION, chance=10000))])
    kill(fight, (1, 0, 0))
    assert listing(Leader().roll_drops(fight, Scripted(1))) == [("Drop", 0, 0, 1, "potion", 1)]


def test_the_snapshot_keeps_the_tables_and_who_buffed_whom():
    table = DropTable("w", "W", weighted=True, rolls=2, entries=(DropEntry(POTION, weight=3, minimum=1, maximum=2, share="each_team"), DropEntry(None, weight=1)))
    fight = hero_fight([table], area=["w"])
    haste = status.StatusSpec("haste", "Haste", status.GOOD)
    fight.statuses["haste"] = haste
    status.place(fight.get((0, 0, 0)), haste, (0, 0, 1), 3)
    raw = json.loads(json.dumps(dehydrate(fight)))
    again = hydrate(raw)
    assert dehydrate(again) == dehydrate(fight) and again.area_drops == ["w"] and again.get((1, 0, 0)).drops == ("w",)
    assert again.drop_tables["w"] == table and again.get((0, 0, 0)).buffed_by == [(0, 0, 1)]
    assert hydrate({k: v for k, v in raw.items() if k not in ("drop_tables", "area_drops")}).drop_tables == {}, "a fight stored before drops"


# --- the seed format --------------------------------------------------------------------------------------------------------------

TABLE = {"key": "spoils", "name": "Spoils", "entries": [{"item": "potion", "chance": 2500, "min": 1, "max": 3, "for": "each_team"}, {"item": "sword", "chance": 10000}]}
BASE = {"items": [{"key": "potion", "name": "Potion"}, {"key": "sword", "name": "Sword", "equip_slots": ["rhand"]}]}


def seed(**changes):
    return {**BASE, "drop_tables": [{**TABLE, **changes}]}


def refused(data, text, **kwargs):
    with pytest.raises(ContentError) as caught:
        check_seed(data, **kwargs)
    assert text in str(caught.value), str(caught.value)


def test_a_drop_table_is_checked_and_defaults_are_filled_in():
    table = check_seed(seed())["drop_tables"][0]
    assert (table.weighted, table.rolls) == (False, 1)
    first, second = table.entries
    assert (first.item, first.chance, first.min, first.max, first.share) == ("potion", 2500, 1, 3, "each_team")
    assert (second.min, second.max, second.share) == (1, 1, "one")
    assert table.model_dump(mode="json", by_alias=True)["entries"][0]["for"] == "each_team", "stored under its own name, for"


@pytest.mark.parametrize("changes, text", [
    ({"entries": []}, "entries"),
    ({"entries": [{"item": "potion", "chance": 0}]}, "a chance is 1 to 10000"),
    ({"entries": [{"item": "potion", "chance": 10001}]}, "a chance is 1 to 10000"),
    ({"entries": [{"chance": 5}]}, "needs an item"),
    ({"entries": [{"item": "potion", "chance": 5, "weight": 2}]}, "weight is for weighted"),
    ({"entries": [{"item": "potion", "chance": 5, "min": 3, "max": 2}]}, "min can't be above max"),
    ({"entries": [{"item": "potion", "chance": 5, "min": 0}]}, "min"),
    ({"entries": [{"item": "potion", "chance": 5, "for": "everyone"}]}, "for"),
    ({"entries": [{"item": "potion", "chance": 5, "share": "one"}]}, "share"),
    ({"rolls": 3}, "rolls is for weighted tables"),
    ({"weighted": True}, "needs a weight"),
    ({"weighted": True, "entries": [{"item": "potion", "weight": 2, "chance": 5}]}, "chance is for plain tables"),
    ({"entries": [{"item": "ghost", "chance": 5}]}, "entries name 'ghost', which items.json doesn't have"),
    ({"surprise": 1}, "surprise"),
])
def test_a_bad_drop_table_is_refused_with_what_is_wrong(changes, text):
    refused(seed(**changes), text)


def test_a_weighted_table_may_drop_nothing_and_a_game_may_set_its_own_scale():
    ok = check_seed(seed(weighted=True, rolls=2, entries=[{"item": "potion", "weight": 3}, {"weight": 7}]))["drop_tables"][0]
    assert ok.rolls == 2 and ok.entries[1].item is None
    assert check_seed(seed(entries=[{"item": "potion", "chance": 100}]), drop_scale=100)["drop_tables"][0].entries[0].chance == 100
    refused(seed(entries=[{"item": "potion", "chance": 101}]), "a chance is 1 to 100", drop_scale=100)


def test_a_monster_names_tables_that_exist():
    monster = {"key": "rat", "name": "Rat", "personality": "plain", "drops": ["spoils"]}
    data = {**seed(), "personalities": [{"key": "plain", "name": "Plain"}], "monsters": [monster]}
    assert check_seed(data)["monsters"][0].drops == ["spoils"]
    refused({**data, "monsters": [{**monster, "drops": ["nope"]}]}, "drops names 'nope', which drop_tables.json doesn't have")
    refused({**data, "drop_tables": [TABLE, TABLE]}, "drop_tables.json: key 'spoils' is used twice")


# --- in the database, to the end of a fight --------------------------------------------------------------------------------------------

PESTS = {
    **SEED,
    "drop_tables": [
        {"key": "scraps", "name": "Scraps", "entries": [{"item": "potion", "chance": 10000, "min": 2, "max": 2}]},
        {"key": "area", "name": "Area", "entries": [{"item": "sword", "chance": 10000}]},
    ],
    "monsters": [*SEED["monsters"], {"key": "pest", "name": "Pest", "personality": "plain", "gold_reward": 3, "drops": ["scraps"],
                                     "stats": {"HP": 1, "MP": 0, "Speed": 4, "Accuracy": 4, "Strength": 2, "Dodge": 1, "Block": 1}}],
}


async def started(db, area=None, full=False):
    from terraforma.heroes import service

    hero = await a_hero(db)
    hero_id, account_id = hero.id, hero.account_id
    await load_content(db, PESTS)  # (this expires what the session holds)
    hero, mike = await db.get(Hero, hero_id), await db.get(Account, account_id)
    team = await service.create_team(db, mike, "Alpha")
    await service.add_to_team(db, mike, team.id, hero.id)
    if full:  # gear never stacks: ten more stacks fill the inventory (it held two)
        sword = await db.scalar(select(Item.id).where(Item.key == "sword"))
        for number in range(10):
            db.add(HeroItem(hero_id=hero.id, item_id=sword, position=2 + number, qty=1))
        await db.flush()
    return hero, mike, await live.start_team_fight(db, team, ["pest"], Rules(), area)


async def play_out(db, record, mike):
    for _ in range(30):
        if record.finished:
            return
        await live.submit_command(db, record, mike.id, (0, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0), RULES)
        await live.resolve_round(db, record, RULES)
    raise AssertionError("the fight went on too long")


async def stacks(db, hero):
    return [(item.key, stack.qty) for stack, item in await inventory.stacks(db, hero)]


async def test_the_tables_are_loaded_and_a_fight_starts_with_them(db):
    await load_content(db, PESTS)
    await db.commit()
    from terraforma.content import models

    row = await db.scalar(select(models.DropTable).where(models.DropTable.key == "scraps"))
    assert (row.weighted, row.rolls, row.entries) == (False, 1, [{"item": "potion", "chance": 10000, "weight": 0, "min": 2, "max": 2, "for": "one"}])
    assert (await db.scalar(select(models.Monster).where(models.Monster.key == "pest"))).drops == ["scraps"]
    tables = await known_drop_tables(db, {"scraps", "area", "nope"})
    assert sorted(tables) == ["area", "scraps"] and tables["scraps"].entries[0].item.key == "potion"
    await load_content(db, {**SEED, "drop_tables": [PESTS["drop_tables"][1]]})
    assert sorted(await known_drop_tables(db, {"scraps", "area"})) == ["area"], "a table the seed dropped is not handed out"


async def test_a_won_fight_puts_the_drops_in_the_inventory_once(db):
    hero, mike, record = await started(db, ["area"])
    state, _played = await store.load_state(db, record, RULES)
    assert sorted(state.drop_tables) == ["area", "scraps"] and state.area_drops == ["area"]
    before = await stacks(db, hero)
    await play_out(db, record, mike)
    after = await stacks(db, hero)
    assert after == [("sword", 1), ("potion", 5), ("sword", 1)] and before == [("sword", 1), ("potion", 3)], "two more potions on the stack and the area's sword (gear never stacks)"
    assert record.drops_saved is True
    await store.apply_results(db, record, RULES)
    assert await stacks(db, hero) == after, "saved once, however often the result is saved"
    assert await store.verify(db, record, RULES, deep=True) > 0


async def test_a_fight_cannot_name_an_area_table_that_does_not_exist(db):
    from terraforma.heroes import service

    hero = await a_hero(db)
    hero_id, account_id = hero.id, hero.account_id
    await load_content(db, PESTS)
    hero, mike = await db.get(Hero, hero_id), await db.get(Account, account_id)
    team = await service.create_team(db, mike, "Alpha")
    await service.add_to_team(db, mike, team.id, hero.id)
    with pytest.raises(live.Refused, match="no drop table 'ghost'"):
        await live.start_team_fight(db, team, ["pest"], Rules(), ["ghost"])


async def test_a_full_inventory_loses_what_does_not_fit_with_a_drop_lost_event_and_saves_what_did(db):
    hero, mike, record = await started(db, ["area"], full=True)  # twelve stacks: the potions stack, the area's sword has no room
    await play_out(db, record, mike)
    events = [(each[0], each[1][3:]) for action in await store.actions(db, record) for each in action.events if each[0] in ("Drop", "DropLost")]
    assert events == [("Drop", ["potion", 2]), ("DropLost", ["sword", 1])]
    held = await stacks(db, hero)
    assert ("potion", 5) in held and len(held) == 12 and held.count(("sword", 1)) == 11, "no thirteenth stack"
