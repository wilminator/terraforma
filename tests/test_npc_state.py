"""The dialog tags that read and change the game's state (items and quests), through a real conversation, on every database."""

import pytest
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.heroes import inventory
from terraforma.heroes import service as heroes
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.parties import service as parties
from terraforma.quests import service as quests
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}],
    "items": [
        {"key": "potion", "name": "Potion", "price": 25, "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "sword", "name": "Sword", "price": 100, "equip_slots": ["hand"], "stat_bonus": {"Strength": 3}},
    ],
}
NPCS = Npcs()


class Party:
    """Mike's Vanguard (Aria) and Rearguard (Bram), and Zed's Scouts (Cleo), in one party in that order: Mike leads."""


async def a_party(db):
    await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    out = Party()
    out.teams, out.heroes = [], []
    for owner, team_name, hero_name in ((mike, "Vanguard", "Aria"), (mike, "Rearguard", "Bram"), (zed, "Scouts", "Cleo")):
        team = await heroes.create_team(db, owner, team_name)
        hero = await heroes.create_hero(db, owner, hero_name, "fighter")
        await heroes.add_to_team(db, owner, team.id, hero.id)
        out.teams.append(team.id)
        out.heroes.append(hero)
    out.party = await parties.create_party(db, out.teams[0], 20)
    await parties.join_party(db, out.party.id, out.teams[1], 20)
    await parties.join_party(db, out.party.id, out.teams[2], 20)
    out.mike, out.zed = mike, zed
    return out


async def npc_says(db, hero, dialog):
    """What the NPC says to the hero, whole (it never asks anything)."""
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, dialog)
    frame = await service.talk(db, NPCS, hero, npc.id)
    assert frame["ended"], frame
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


def check(command):
    return f"`{command}`yes`jump,end``label,no`no"


# --- items ---------------------------------------------------------------------------------------------------------------

async def test_have_item_any_holds_when_any_hero_of_the_party_has_it(db):
    p = await a_party(db)
    dialog = check("have_item,potion,1,any,no")
    assert await npc_says(db, p.heroes[0], dialog) == "no"
    await inventory.add_item(db, p.heroes[2], "potion", 1)  # Cleo, of the Scouts: the last team
    assert await npc_says(db, p.heroes[0], dialog) == "yes"


async def test_the_quantity_is_what_one_hero_holds(db):
    p = await a_party(db)
    await inventory.add_item(db, p.heroes[0], "potion", 1)
    await inventory.add_item(db, p.heroes[1], "potion", 1)
    assert await npc_says(db, p.heroes[0], check("have_item,potion,2,any,no")) == "no", "two heroes with one each is not a hero with two"
    await inventory.add_item(db, p.heroes[1], "potion", 1)
    assert await npc_says(db, p.heroes[0], check("have_item,potion,2,any,no")) == "yes"


async def test_have_item_lead_looks_at_the_leading_team_only(db):
    p = await a_party(db)
    dialog = check("have_item,potion,1,lead,no")
    await inventory.add_item(db, p.heroes[2], "potion", 1)  # the Scouts are Zed's: not the leading team
    assert await npc_says(db, p.heroes[0], dialog) == "no"
    await inventory.add_item(db, p.heroes[0], "potion", 1)  # Vanguard is Mike's first team
    assert await npc_says(db, p.heroes[2], dialog) == "yes", "whoever talks, the party's lead team is the one asked"


async def test_have_item_each_needs_a_hero_of_every_team(db):
    p = await a_party(db)
    dialog = check("have_item,potion,1,each,no")
    await inventory.add_item(db, p.heroes[0], "potion", 1)
    await inventory.add_item(db, p.heroes[1], "potion", 1)
    assert await npc_says(db, p.heroes[0], dialog) == "no"
    await inventory.add_item(db, p.heroes[2], "potion", 1)
    assert await npc_says(db, p.heroes[0], dialog) == "yes"


async def test_in_a_town_a_team_is_asked_about_only_its_own_group(db):
    p = await a_party(db)
    await inventory.add_item(db, p.heroes[2], "potion", 1)
    dialog = check("have_item,potion,1,any,no")
    assert await npc_says(db, p.heroes[0], dialog) == "yes"
    await towns_service.enter_town(db, Towns(), p.party.id)
    assert await npc_says(db, p.heroes[0], dialog) == "no", "the Scouts are somewhere else in town: an ethereal party of one team"
    assert await npc_says(db, p.heroes[2], dialog) == "yes"


async def test_a_hero_on_no_team_stands_alone(db):
    p = await a_party(db)
    loner = await heroes.create_hero(db, p.mike, "Loner", "fighter")
    await inventory.add_item(db, loner, "potion", 1)
    assert await npc_says(db, loner, check("have_item,potion,1,each,no")) == "yes"
    assert await npc_says(db, loner, check("quests,hunt,any,eq,0,any,no")) == "no", "no team, no record: not even a count of 0"


async def test_add_item_puts_items_in_the_talking_heros_pack_and_jumps_when_they_do_not_all_fit(db):
    p = await a_party(db)
    hero = p.heroes[0]
    assert await npc_says(db, hero, check("add_item,potion,5,no")) == "yes"
    assert sum(stack.qty for stack, _item in await inventory.stacks(db, hero)) == 5
    assert await npc_says(db, hero, check("add_item,potion,9999,no")) == "no"
    assert sum(stack.qty for stack, _item in await inventory.stacks(db, hero)) == 5, "all or nothing"
    for _ in range(inventory.MAX_ITEMS):  # fill the pack with swords (one to a place)
        if await inventory.add_item(db, hero, "sword", 1):
            break
    assert await npc_says(db, hero, check("add_item,sword,1,no")) == "no"
    assert await npc_says(db, p.heroes[1], check("add_item,sword,1,no")) == "yes", "someone else's pack is theirs"


async def test_remove_item_takes_from_the_talking_hero_only_if_they_have_enough(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await inventory.add_item(db, hero, "potion", 3)
    await inventory.add_item(db, p.heroes[1], "potion", 5)
    assert await npc_says(db, hero, check("remove_item,potion,4,no")) == "no", "a teammate's potions are not theirs to give"
    assert await npc_says(db, hero, check("remove_item,potion,2,no")) == "yes"
    assert [stack.qty for stack, _item in await inventory.stacks(db, hero)] == [1]
    assert await npc_says(db, hero, check("remove_item,potion,1,no")) == "yes"
    assert await inventory.stacks(db, hero) == [], "an emptied stack goes"


async def test_remove_item_takes_from_every_stack_it_needs(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await inventory.add_item(db, hero, "sword", 1)
    await inventory.add_item(db, hero, "sword", 1)
    assert await npc_says(db, hero, check("remove_item,sword,2,no")) == "yes"
    assert await inventory.stacks(db, hero) == []


async def test_an_item_that_does_not_exist_ends_the_conversation_with_the_reason(db):
    p = await a_party(db)
    npc = await service.place_npc(db, "keeper", "Keeper", p.heroes[0].map_id, 0, 1, check("have_item,unicorn,1,any,no"))
    with pytest.raises(service.NpcError, match="no item 'unicorn'"):
        await service.talk(db, NPCS, p.heroes[0], npc.id)
    assert await service.current(db, p.heroes[0]) == {"talking": False}


# --- quests --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("op, n, expected", [("eq", 3, True), ("eq", 2, False), ("ne", 2, True), ("ne", 3, False), ("lt", 4, True), ("lt", 3, False),
                                             ("le", 3, True), ("le", 2, False), ("gt", 2, True), ("gt", 3, False), ("ge", 3, True), ("ge", 4, False)])
async def test_quests_compare_the_completed_count_with_every_comparison(db, op, n, expected):
    p = await a_party(db)
    await quests.complete(db, p.teams[0], "hunt", 2, times=3)
    result = await npc_says(db, p.heroes[0], check(f"quests,hunt,2,{op},{n},lead,no"))
    assert result == ("yes" if expected else "no")


async def test_quests_count_one_level_or_every_level(db):
    p = await a_party(db)
    await quests.complete(db, p.teams[0], "hunt", 1, times=2)
    await quests.complete(db, p.teams[0], "hunt", 5, times=3)
    assert await npc_says(db, p.heroes[0], check("quests,hunt,1,eq,2,lead,no")) == "yes"
    assert await npc_says(db, p.heroes[0], check("quests,hunt,any,eq,5,lead,no")) == "yes"
    assert await npc_says(db, p.heroes[0], check("quests,hunt,9,eq,0,lead,no")) == "yes", "none completed at level 9 is a count of 0"
    assert await npc_says(db, p.heroes[0], check("quests,fetch,any,ge,1,lead,no")) == "no"


async def test_quest_scopes_any_lead_and_each(db):
    p = await a_party(db)
    dialog = lambda scope: check(f"quests,hunt,1,ge,1,{scope},no")  # noqa: E731
    await quests.complete(db, p.teams[2], "hunt", 1)  # only the Scouts have done it
    assert [await npc_says(db, p.heroes[0], dialog(scope)) for scope in ("any", "lead", "each")] == ["yes", "no", "no"]
    await quests.complete(db, p.teams[0], "hunt", 1)  # and now the lead team (Vanguard)
    assert [await npc_says(db, p.heroes[0], dialog(scope)) for scope in ("any", "lead", "each")] == ["yes", "yes", "no"]
    await quests.complete(db, p.teams[1], "hunt", 1)
    assert [await npc_says(db, p.heroes[0], dialog(scope)) for scope in ("any", "lead", "each")] == ["yes", "yes", "yes"]


async def test_quest_markers_are_set_and_compared_per_team(db):
    p = await a_party(db)
    aria, bram = p.heroes[0], p.heroes[1]
    assert await npc_says(db, aria, check("quest_marker,rats,eq,0,lead,no")) == "yes", "no marker is 0"
    assert await npc_says(db, aria, "`set_quest_marker,rats,2`Go and kill two rats.") == "Go and kill two rats."
    assert await quests.marker(db, p.teams[0], "rats") == 2 and await quests.marker(db, p.teams[1], "rats") == 0, "Aria's team, not Bram's"
    assert await npc_says(db, aria, check("quest_marker,rats,ge,2,lead,no")) == "yes"
    assert await npc_says(db, bram, check("quest_marker,rats,ge,2,any,no")) == "yes", "any team of the party"
    assert await npc_says(db, bram, check("quest_marker,rats,ge,2,each,no")) == "no"
    await npc_says(db, aria, "`set_quest_marker,rats,0`")
    assert await quests.marker(db, p.teams[0], "rats") == 0


async def test_a_hero_on_no_team_has_no_quest_markers_to_set(db):
    p = await a_party(db)
    loner = await heroes.create_hero(db, p.mike, "Loner", "fighter")
    npc = await service.place_npc(db, "keeper", "Keeper", loner.map_id, loner.x, loner.y + 1, "`set_quest_marker,rats,1`")
    with pytest.raises(service.NpcError, match="no team"):
        await service.talk(db, NPCS, loner, npc.id)
