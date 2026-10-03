"""Challenge Tokens and gold in the dialog: have_tokens, spend_tokens and add_gold, and a step that is all or nothing, on every database."""

import pytest

from terraforma.challenge import service as challenge
from terraforma.economy import TeamGold
from terraforma.heroes import inventory
from terraforma.heroes.models import Team
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.npcs.script import ScriptError, parse

from .test_npc_state import a_party

pytestmark = pytest.mark.anyio

NPCS, ECONOMY = Npcs(), TeamGold()


async def says(db, hero, dialog, economy=ECONOMY):
    """What the NPC says to the hero, whole."""
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, dialog)
    frame = await service.talk(db, NPCS, hero, npc.id, None, economy)
    assert frame["ended"], frame
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


def check(command):
    return f"`{command}`yes`jump,end``label,no`no"


async def fund(db, hero, tokens):
    assert await challenge.change(db, hero.account_id, tokens, challenge.PURCHASE, key=f"fund-{hero.id}-{tokens}")


async def potions(db, hero):
    return sum(stack.qty for stack, _item in await inventory.stacks(db, hero))


async def test_have_tokens_asks_the_talking_heros_account(db):
    p = await a_party(db)
    assert await says(db, p.heroes[0], check("have_tokens,3,no")) == "no"
    await fund(db, p.heroes[0], 3)
    assert await says(db, p.heroes[0], check("have_tokens,3,no")) == "yes"
    assert await says(db, p.heroes[0], check("have_tokens,4,no")) == "no"
    assert await says(db, p.heroes[2], check("have_tokens,1,no")) == "no", "Zed's account has none: tokens are per account"


async def test_spend_tokens_takes_them_and_records_it_and_goes_to_the_label_without_enough(db):
    p = await a_party(db)
    await fund(db, p.heroes[0], 5)
    assert await says(db, p.heroes[0], check("spend_tokens,2,no")) == "yes"
    assert await challenge.balance(db, p.mike.id) == 3
    assert await says(db, p.heroes[0], check("spend_tokens,4,no")) == "no"
    assert await challenge.balance(db, p.mike.id) == 3, "a refused spend takes nothing"
    assert await challenge.audit(db, p.mike.id), "the ledger adds up to the balance"


async def test_add_gold_pays_the_economys_purse_and_needs_an_economy(db):
    p = await a_party(db)
    assert await says(db, p.heroes[0], check("add_gold,40,no")) == "yes"
    team = await db.get(Team, p.teams[0])
    await db.refresh(team, ["gold"])
    assert team.gold == 40
    with pytest.raises(service.NpcError, match="no economy"):
        await says(db, p.heroes[0], check("add_gold,40,no"), economy=None)


async def test_a_step_that_fails_undoes_what_it_did_before_failing(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await fund(db, hero, 5)
    with pytest.raises(service.NpcError, match="no status"):
        await says(db, hero, "`add_item,potion,2,end``spend_tokens,5,end``add_gold,10,end``add_status,hero,nope,60,removable,end`done")
    assert await potions(db, hero) == 0 and await challenge.balance(db, p.mike.id) == 5, "the item and the tokens are back"
    team = await db.get(Team, p.teams[0])
    await db.refresh(team, ["gold"])
    assert team.gold == 0


async def test_a_perk_sells_for_tokens_in_one_dialog(db):
    p = await a_party(db)
    hero = p.heroes[0]
    perk = "`spend_tokens,3,poor``add_gold,100,end`Here is your gold.`jump,end``label,poor`Come back with 3 tokens."
    assert await says(db, hero, perk) == "Come back with 3 tokens."
    await fund(db, hero, 3)
    assert await says(db, hero, perk) == "Here is your gold."
    assert await challenge.balance(db, p.mike.id) == 0
    assert await says(db, hero, perk) == "Come back with 3 tokens.", "spent"


def test_the_tags_are_checked_when_the_text_is_parsed():
    for bad in ("`have_tokens,0,no`x", "`spend_tokens,two,no`x", "`add_gold,5`x", "`spend_tokens,1,no,extra`x"):
        with pytest.raises(ScriptError):
            parse(bad)
    parse("`have_tokens,1,no``spend_tokens,1,no``add_gold,1,no``label,no`x")
