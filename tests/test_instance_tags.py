"""The dialog tags that put a party into a quest instance, win it and leave it, on every database."""

import pytest
from sqlalchemy import select

from terraforma.instances import service as instances
from terraforma.models import Map
from terraforma.npcs.script import ScriptError, parse
from terraforma.npcs.service import NpcError

from .test_guild_ranks import says
from .test_npc_state import a_party

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


async def crypt(db, hero):
    here = await db.get(Map, hero.map_id)
    found = Map(world_id=here.world_id, name="crypt", width=10, height=10)
    db.add(found)
    await db.flush()
    return found


def test_the_instance_tags_are_checked_when_the_text_is_placed():
    parse("`enter_instance,rats,private,crypt,1,1,1,0,no``label,no`")
    parse("`complete_instance,no``leave_instance,no``label,no`")
    parse("`instance_won,other,no``instance_won,self,no``label,no`")
    for bad in (
        "`enter_instance,rats,solo,crypt,1,1,1,0,end`",  # not a mode
        "`enter_instance,rats,shared,crypt,1,1,0,0,end`",  # no room for anyone
        "`enter_instance,rats,shared,crypt,1,1,2,end`",  # too few parameters
        "`complete_instance,nowhere`",  # label never defined
        "`instance_won,both,end`",  # not a winner
        "`instance_won,other`",  # too few parameters
    ):
        with pytest.raises(ScriptError):
            parse(bad)


async def test_a_dialog_enters_wins_and_leaves_a_private_instance(db):
    p = await a_party(db)
    hero = p.heroes[0]
    found = await crypt(db, hero)
    start = (hero.map_id, hero.x, hero.y)
    told = await says(db, hero, "Down you go.`enter_instance,rats,private,crypt,2,3,1,0,no``end``label,no`Not now.")
    assert told == "Down you go."
    assert (await instances.instance_of(db, p.party.id)).quest == "rats"
    await db.refresh(hero)
    assert (hero.map_id, hero.x, hero.y) == (found.id, 2, 3)
    assert await says(db, hero, "`complete_instance,no`Done.`end``label,no`Not yours.") == "Done."
    assert await says(db, hero, "`complete_instance,no`Done.`end``label,no`Won already.") == "Won already."
    assert await says(db, hero, "`leave_instance,no`Out.`end``label,no`Not in one.") == ""
    assert await instances.instance_of(db, p.party.id) is None
    await db.refresh(hero)
    assert (hero.map_id, hero.x, hero.y) == start


async def test_a_dialog_that_cannot_enter_goes_to_its_label(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await crypt(db, hero)
    enter = "`enter_instance,rats,private,crypt,2,3,1,0,no`Never seen.`end``label,no`Cannot."
    assert await says(db, hero, enter) == ""
    assert await says(db, hero, enter) == "Cannot.", "already in an instance"
    assert await says(db, hero, "`leave_instance,no`x`end``label,no`Not in one.") == ""
    assert await says(db, hero, "`leave_instance,no`x`end``label,no`Not in one.") == "Not in one."
    assert await says(db, hero, "`complete_instance,no`x`end``label,no`Not in one.") == "Not in one."


async def test_a_public_quest_moves_no_one_and_the_dialog_goes_on(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await crypt(db, hero)
    assert await says(db, hero, "`enter_instance,rats,public,crypt,2,3,1,0,no`Walk there yourself.`end``label,no`Cannot.") == "Walk there yourself."
    assert await instances.instance_of(db, p.party.id) is None


async def test_a_map_that_is_not_there_is_a_mistake_in_the_text(db):
    p = await a_party(db)
    with pytest.raises(NpcError, match="no map 'nowhere'"):
        await says(db, p.heroes[0], "`enter_instance,rats,private,nowhere,1,1,1,0,end`")
    assert (await db.scalars(select(Map).where(Map.name == "nowhere"))).first() is None


async def test_a_dialog_pays_tokens_up_to_the_purse_and_never_to_an_admin(db):
    from terraforma.challenge import service as challenge
    from terraforma.fights.rules import Rules

    class Small(Rules):
        def challenge_purse_cap(self, account_id):
            return 5

    p = await a_party(db)
    hero = p.heroes[0]
    pay = "`add_tokens,3,no`Paid.`end``label,no`No room."
    assert await says(db, hero, pay, Small()) == "Paid."
    assert await challenge.balance(db, hero.account_id) == 3
    assert await says(db, hero, pay, Small()) == "Paid.", "cut to the 2 that fit"
    assert await challenge.balance(db, hero.account_id) == 5
    assert await says(db, hero, pay, Small()) == "No room."
    assert await challenge.audit(db, hero.account_id)
    await challenge.set_admin(db, p.zed.id)
    assert await says(db, p.heroes[2], pay, Small()) == "No room.", "an admin earns none this way"
    assert await challenge.balance(db, p.zed.id) == 0


async def test_a_dialog_tells_the_losers_of_a_shared_instance_and_the_winner_apart(db):
    p = await a_party(db)
    hero = p.heroes[0]
    await crypt(db, hero)
    other = "`instance_won,other,no`A party beat you to it.`end``label,no`Nobody has."
    mine = "`instance_won,self,no`You won it.`end``label,no`Not you."
    assert await says(db, hero, other) == "Nobody has.", "in no instance"
    assert await says(db, hero, "`enter_instance,rats,shared,crypt,2,3,2,60,no``end``label,no`x") == ""
    assert await says(db, hero, other) == "Nobody has.", "not won yet"
    assert await says(db, hero, mine) == "Not you."
    instance = await instances.instance_of(db, p.party.id)
    instance.status, instance.winner_party_id = instances.WON, p.party.id + 1000
    await db.flush()
    assert await says(db, hero, other) == "A party beat you to it."
    assert await says(db, hero, mine) == "Not you."
    instance.winner_party_id = p.party.id
    await db.flush()
    assert await says(db, hero, other) == "Nobody has."
    assert await says(db, hero, mine) == "You won it."
