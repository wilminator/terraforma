"""Scripted NPC fights: a dialog starts a fight against monsters it names (``npc_fight``) and a later dialog asks how it ended
(``npc_fight_result``). On every database."""

from datetime import datetime

import pytest
from sqlalchemy import func, select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import store
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fighttags import service as fighttags
from terraforma.fighttags.models import FightTeam
from terraforma.heroes import service as heroes
from terraforma.npcs.fight_models import ScriptedFight
from terraforma.npcs.script import ScriptError, parse
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns

from .test_quest_boss_fights import RULES, a_world, says

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


def test_the_fight_tags_are_checked_when_the_dialog_is_parsed():
    parse("`npc_fight,gate,rat,end``npc_fight,gate,rat+rat+ogre,end``npc_fight_result,gate,won,end``npc_fight_result,gate,lost,end``npc_fight_result,gate,running,end`done")
    for bad, why in (("`npc_fight,gate,rat`", "takes 3 parameters"), ("`npc_fight,,rat,end`", "a name for the fight"), ("`npc_fight,gate,,end`", "a monster key"),
                     ("`npc_fight,gate,rat++rat,end`", "a monster key"), (f"`npc_fight,gate,{'+'.join(['rat'] * 21)},no`", "1 to 20 monsters"),
                     ("`npc_fight_result,gate,drawn,end`", "the state"), ("`npc_fight_result,gate,won`", "takes 3 parameters")):
        with pytest.raises(ScriptError, match=why):
            parse(bad)


async def test_the_party_fights_the_named_monsters_and_every_team_of_it_is_tied_to_the_fight(db):
    p = await a_world(db)
    assert await says(db, p.heroes[0], "npc_fight,gate,rat+rat,no") == "", "the dialog is over: the party is in a fight"
    record = await db.scalar(select(FightRecord))
    foes = (await db.scalars(select(FightParticipant.monster_key).where(FightParticipant.fight_id == record.id, FightParticipant.party == 1))).all()
    assert sorted(foes) == ["rat", "rat"]
    assert sorted((await db.scalars(select(ScriptedFight.team_id).where(ScriptedFight.fight_id == record.id))).all()) == sorted(p.teams)
    fight, _ = await store.load_state(db, record, RULES)
    assert sorted(fight.parties[0].teams) == sorted(p.teams)


async def test_a_party_that_cannot_fight_goes_to_the_label(db):
    p = await a_world(db)
    await towns_service.enter_town(db, Towns(), p.party.id)
    assert await says(db, p.heroes[0], "npc_fight,gate,rat,no") == "no", "apart in a town"
    assert await db.scalar(select(func.count()).select_from(FightRecord)) == 0


async def test_the_fight_needs_the_games_rules(db):
    from terraforma.npcs.service import NpcError

    p = await a_world(db)
    with pytest.raises(NpcError, match="needs the game's rules"):
        await says(db, p.heroes[0], "npc_fight,gate,rat,no", rules=None)


async def finish(db, record, *, winners=()):
    """The fight is over, scored as the given teams winning (the dice are not what is tested here)."""
    record.finished = True
    for team_id in (await db.scalars(select(ScriptedFight.team_id).where(ScriptedFight.fight_id == record.id))).all():
        db.add(FightTeam(fight_id=record.id, team_id=team_id, party=0, pvp=False, won=team_id in winners, overkill=0.0, underdog=0.0))
    await db.flush()


async def test_the_result_is_read_per_team_and_per_name(db):
    p = await a_world(db)
    await says(db, p.heroes[0], "npc_fight,gate,rat,no")
    record = await db.scalar(select(FightRecord))
    await finish(db, record, winners=(p.teams[0],))  # (a hero in a running fight cannot talk, so it is asked once the fight is over)

    async def asks(hero, name, state):
        return await says(db, hero, f"npc_fight_result,{name},{state},no")

    assert await asks(p.heroes[0], "gate", "won") == "yes", "Vanguard won"
    assert await asks(p.heroes[0], "gate", "lost") == "no"
    assert await asks(p.heroes[1], "gate", "won") == "no" and await asks(p.heroes[1], "gate", "lost") == "yes", "Rearguard did not"
    assert await asks(p.heroes[0], "gate", "running") == "no", "it is over"
    assert await asks(p.heroes[0], "other", "won") == "no", "no fight under that name"


async def test_a_fight_that_is_not_over_is_running_for_a_hero_who_can_still_talk(db):
    p = await a_world(db)
    await says(db, p.heroes[0], "npc_fight,gate,rat,no")
    record = await db.scalar(select(FightRecord))
    for row in (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == record.id, FightParticipant.hero_id == p.heroes[0].id))).all():
        row.fled = True  # (Aria fled: she is free to talk, the fight goes on)
    await db.flush()
    assert await says(db, p.heroes[0], "npc_fight_result,gate,running,no") == "yes"
    assert await says(db, p.heroes[0], "npc_fight_result,gate,won,no") == "no"
    assert await says(db, p.heroes[0], "npc_fight_result,gate,lost,no") == "no"


async def test_the_latest_fight_under_a_name_is_the_one_that_counts(db):
    p = await a_world(db)
    await says(db, p.heroes[0], "npc_fight,gate,rat,no")
    await finish(db, await db.scalar(select(FightRecord)), winners=())
    assert await says(db, p.heroes[0], "npc_fight_result,gate,lost,no") == "yes"
    await says(db, p.heroes[0], "npc_fight,gate,rat,no")
    second = (await db.scalars(select(FightRecord).order_by(FightRecord.id.desc()).limit(1))).one()
    await finish(db, second, winners=(p.teams[0],))
    assert await says(db, p.heroes[0], "npc_fight_result,gate,won,no") == "yes", "the new fight replaces the old answer"


async def test_the_links_go_with_a_deleted_team_and_a_swept_fight(db):
    p = await a_world(db)
    await says(db, p.heroes[0], "npc_fight,gate,rat,no")
    record = await db.scalar(select(FightRecord))
    await finish(db, record, winners=(p.teams[0],))
    await heroes.delete_team(db, p.zed, p.teams[2])
    assert await db.scalar(select(func.count()).select_from(ScriptedFight)) == 2
    record.updated_at = datetime(2000, 1, 1)
    await db.flush()
    assert await fighttags.sweep(db, RULES, 0) == 1
    assert await db.scalar(select(func.count()).select_from(ScriptedFight)) == 0


def test_the_seed_refuses_a_dialog_that_fights_a_monster_it_does_not_have():
    from .test_realms import seed

    data = seed()
    data["npcs"] = [{"key": "guard", "name": "Guard", "map": "hub", "x": 0, "y": 0, "dialog": "`npc_fight,gate,rat+dragon,end`"}]
    with pytest.raises(ContentError, match="fights 'rat'|fights 'dragon'"):
        check_seed(data)
