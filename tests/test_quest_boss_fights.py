"""The boss fight of a quest the engine made: a dialog starts the party's fight against the quest's promoted bosses, and the quest is settled by how
the fight ended. On every database."""

import pytest
from sqlalchemy import func, select, update

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.fights import live, store
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.fighttags.models import FightTeam
from terraforma.fighttags import service as fighttags
from terraforma.heroes import inventory
from terraforma.heroes import service as heroes
from terraforma.models import World
from terraforma.npcs import service
from terraforma.parties import service as parties
from terraforma.quests import bossfight, bosses, rotation
from terraforma.quests import service as quests
from terraforma.quests.boss_models import QuestBossFight
from terraforma.quests.hooks import RotatingQuests
from terraforma.quests.rotation_models import TeamSlotQuest
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.world.start import ensure_start

from .test_fight_store import SEED as FIGHT_SEED
from .test_live_fights import FOE
from .test_npc_state import NPCS, PASSWORD, REACH, check

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

RULES = Rules()
DAY = rotation.DAY
SLOT = {"key": "rank-s", "name": "Rank S promotion", "guild": "vanguard", "rank": 5, "min_days": 10, "max_days": 20, "grace_days": 3, "limit_days": 5}
#: The bosses of the quest: a rat at its own strength (one hit), and a quest that is not the engine's (no bosses).
RAT_KING = {"key": "rat-king", "name": "Rat King", "slot": "rank-s", "spec": {"bosses": [{"monster": "rat", "scale": 1.0, "pxp": 1, "extras": {}}]}}
PLAIN = {"key": "plain", "name": "Plain", "slot": "rank-s", "spec": {"goal": "something the game knows"}}


async def a_world(db, *templates):
    """The content, the slot and templates, and the world's seed fixed. Mike's Vanguard (Aria, with a sword) and Rearguard (Bram) and Zed's Scouts (Cleo)
    stand in one party in that order."""
    await load_content(db, {**FIGHT_SEED, "quest_slots": [SLOT], "quest_templates": list(templates)})
    await ensure_start(db)
    await db.execute(update(World).values(seed=4242))
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    out = type("Party", (), {})()
    out.teams, out.heroes = [], []
    for owner, team_name, hero_name in ((mike, "Vanguard", "Aria"), (mike, "Rearguard", "Bram"), (zed, "Scouts", "Cleo")):
        team = await heroes.create_team(db, owner, team_name)
        hero = await heroes.create_hero(db, owner, hero_name, "fighter")
        await inventory.add_item(db, hero, "sword", 1)
        await inventory.equip(db, hero, 0, 0)
        await heroes.add_to_team(db, owner, team.id, hero.id)
        out.teams.append(team.id)
        out.heroes.append(hero)
    out.party = await parties.create_party(db, out.teams[0], 20)
    await parties.join_party(db, out.party.id, out.teams[1], 20)
    await parties.join_party(db, out.party.id, out.teams[2], 20)
    out.mike, out.zed = mike, zed
    return out


async def says(db, hero, command, rules=RULES):
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, check(command))
    frame = await service.talk(db, NPCS, REACH, hero, npc.id, rules=rules)
    assert frame["ended"], frame
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


async def took(db, team_id):
    """The team takes the quest the slot holds."""
    progress = await rotation.offer(db, RotatingQuests(), team_id, "rank-s")
    await rotation.start(db, RotatingQuests(), progress.id)
    return progress


async def ended(db, record, *, winners=()):
    """The fight is over, scored as the given teams winning (the dice are not what is tested here)."""
    record.finished = True
    for row in (await db.scalars(select(TeamSlotQuest).join(QuestBossFight, QuestBossFight.progress_id == TeamSlotQuest.id).where(QuestBossFight.fight_id == record.id))).all():
        db.add(FightTeam(fight_id=record.id, team_id=row.team_id, party=0, pvp=False, won=row.team_id in winners, overkill=0.0, underdog=0.0))
    await db.flush()


# --- the dialog tag ----------------------------------------------------------------------------------------------------

def test_the_slot_boss_tag_is_checked_when_the_dialog_is_parsed():
    from terraforma.npcs.script import ScriptError, parse
    parse("`slot_boss,rank-s,fight,end``slot_boss,rank-s,won,end``slot_boss,rank-s,lost,end`done")
    for bad, why in (("`slot_boss,rank-s,fight`", "takes 3 parameters"), ("`slot_boss,rank-s,flee,end`", "the action"),
                     ("`slot_boss,,fight,end`", "a quest slot key"), ("`slot_boss,rank-s,fight,nowhere`", "nowhere")):
        with pytest.raises(ScriptError, match=why):
            parse(bad)


# --- the bosses as fighters --------------------------------------------------------------------------------------------

async def test_boss_entries_become_fighters_with_the_scaled_stats(db):
    await a_world(db)
    [plain, big] = await bosses.fighters(db, RULES, [{"monster": "ogre", "scale": 1.0, "extras": {}}, {"monster": "ogre", "scale": 2.5, "extras": {}}])
    assert plain.monster == big.monster == "ogre"
    assert big.base["HP"] == round(60 * 2.5) and plain.base["HP"] == 60
    assert big.current["HP"] == big.base["HP"], "it starts the fight whole"


async def test_a_boss_the_world_lost_is_an_error(db):
    await a_world(db)
    with pytest.raises(bosses.BossError, match="not a monster the world has"):
        await bosses.fighters(db, RULES, [{"monster": "dragon", "scale": 1.0, "extras": {}}])


async def test_a_party_fight_can_take_ready_made_foes(db):
    p = await a_world(db)
    [boss] = await bosses.fighters(db, RULES, [{"monster": "ogre", "scale": 3.0, "extras": {}}])
    record = await live.start_party_fight(db, p.party.id, [], RULES, foes=[boss])
    fight, _played = await store.load_state(db, record, RULES)
    assert fight.parties[1].groups[0].characters[0].base["HP"] == 180
    assert (await db.scalar(select(FightParticipant.monster_key).where(FightParticipant.fight_id == record.id, FightParticipant.party == 1))) == "ogre"
    with pytest.raises(live.Refused, match="between 1 and"):
        await live.start_party_fight(db, p.party.id, [], RULES, foes=[])


# --- starting the fight ------------------------------------------------------------------------------------------------

async def test_the_party_fights_the_bosses_of_the_quest_the_team_took_and_every_team_on_that_quest_is_tied_to_the_fight(db):
    p = await a_world(db, RAT_KING)
    one, two = await took(db, p.teams[0]), await took(db, p.teams[1])  # (the Scouts took none)
    assert await says(db, p.heroes[0], "slot_boss,rank-s,fight,no") == "", "the dialog is over: the party is in a fight"
    record = await db.scalar(select(FightRecord))
    assert (await db.scalar(select(FightParticipant.monster_key).where(FightParticipant.fight_id == record.id, FightParticipant.party == 1))) == "rat"
    tied = (await db.scalars(select(QuestBossFight.progress_id).where(QuestBossFight.fight_id == record.id))).all()
    assert sorted(tied) == sorted([one.id, two.id])
    fight, _ = await store.load_state(db, record, RULES)
    assert sorted(fight.parties[0].teams) == sorted(p.teams), "the whole party fights, the Scouts too"


async def test_a_team_with_no_quest_of_the_slot_or_a_quest_without_bosses_goes_to_the_label(db):
    p = await a_world(db, PLAIN)
    assert await says(db, p.heroes[0], "slot_boss,rank-s,fight,no") == "no", "took nothing"
    await took(db, p.teams[0])
    assert await says(db, p.heroes[0], "slot_boss,rank-s,fight,no") == "no", "the quest is the game's own: no bosses"
    assert await db.scalar(select(func.count()).select_from(FightRecord)) == 0


async def test_a_party_apart_in_a_town_goes_to_the_label(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await towns_service.enter_town(db, Towns(), p.party.id)
    assert await says(db, p.heroes[0], "slot_boss,rank-s,fight,no") == "no"
    assert await db.scalar(select(func.count()).select_from(FightRecord)) == 0


async def test_a_party_already_in_a_fight_is_refused_and_no_second_fight_is_made(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    with pytest.raises(live.Refused, match="already in a fight"):
        await bossfight.start(db, RotatingQuests(), RULES, p.party.id, tuple(p.teams), "rank-s", p.teams[0])
    assert await db.scalar(select(func.count()).select_from(FightRecord)) == 1
    assert await db.scalar(select(func.count()).select_from(QuestBossFight)) == 1, "(no link for the fight that was refused)"


async def test_the_fight_needs_the_games_rules(db):
    from terraforma.npcs.service import NpcError

    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    with pytest.raises(NpcError, match="needs the game's rules"):
        await says(db, p.heroes[0], "slot_boss,rank-s,fight,no", rules=None)


async def test_an_unknown_slot_is_an_error_the_dialog_reports(db):
    from terraforma.npcs.service import NpcError

    p = await a_world(db)
    with pytest.raises(NpcError, match="no such quest slot"):
        await says(db, p.heroes[0], "slot_boss,nowhere,fight,no")


# --- settling ----------------------------------------------------------------------------------------------------------

async def test_a_fight_still_running_settles_nothing(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    # (nobody in a running fight can talk to an NPC, but a hero who fled it can, so the answer must be "not yet")
    assert await bossfight.result(db, RotatingQuests(), p.teams[0], "rank-s") is None
    assert not await bossfight.settle(db, RotatingQuests(), p.teams[0], "rank-s", bossfight.WON)
    assert not await bossfight.settle(db, RotatingQuests(), p.teams[0], "rank-s", bossfight.LOST)
    assert (await rotation.active(db, p.teams[0], "rank-s")) is not None


async def test_a_won_fight_finishes_the_quest_as_won_for_the_teams_that_won_and_as_failed_for_the_others(db):
    p = await a_world(db, RAT_KING)

    class Penalties(RotatingQuests):
        def __init__(self):
            self.charged = []

        async def penalty(self, session, team_id, slot, reason):
            self.charged.append((team_id, reason))

    db.info["rotating_quests"] = hooks = Penalties()
    for team_id in p.teams[:2]:
        await took(db, team_id)
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    await ended(db, await db.scalar(select(FightRecord)), winners=(p.teams[0],))
    assert await says(db, p.heroes[0], "slot_boss,rank-s,lost,no") == "no", "Vanguard won"
    assert await says(db, p.heroes[0], "slot_boss,rank-s,won,no") == "yes"
    assert await quests.completed(db, p.teams[0], "rank-s", 5) == 1 and hooks.charged == []
    assert await says(db, p.heroes[1], "slot_boss,rank-s,won,no") == "no", "Rearguard did not win"
    assert await says(db, p.heroes[1], "slot_boss,rank-s,lost,no") == "yes"
    assert hooks.charged == [(p.teams[1], "poor_performance")] and await quests.completed(db, p.teams[1], "rank-s", 5) == 0
    assert await says(db, p.heroes[0], "slot_boss,rank-s,won,no") == "no", "the quest is finished: nothing more to settle"


async def test_a_fight_the_team_fled_or_was_not_scored_in_counts_as_lost(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    await ended(db, await db.scalar(select(FightRecord)))
    assert await bossfight.result(db, RotatingQuests(), p.teams[0], "rank-s") == bossfight.LOST


async def test_a_quest_that_ran_out_of_time_has_failed_whatever_the_fight_says(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    await ended(db, await db.scalar(select(FightRecord)), winners=(p.teams[0],))
    await db.execute(update(TeamSlotQuest).values(deadline=wallclock.timestamp() - 1))
    assert await says(db, p.heroes[0], "slot_boss,rank-s,won,no") == "no", "(the quest timed out first)"
    assert await quests.completed(db, p.teams[0], "rank-s", 5) == 0


async def test_a_lost_fight_fails_the_quest_for_its_period(db):
    p = await a_world(db, RAT_KING)
    progress = await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    await ended(db, await db.scalar(select(FightRecord)))
    assert await says(db, p.heroes[0], "slot_boss,rank-s,lost,no") == "yes"
    assert (await db.get(TeamSlotQuest, progress.id)).status == "failed"


# --- played out --------------------------------------------------------------------------------------------------------

async def test_a_boss_fight_played_out_is_won_by_the_party_and_settles_the_quest(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    record = await db.scalar(select(FightRecord))
    mike = p.mike
    for _ in range(30):
        if record.finished:
            break
        await live.submit_command(db, record, mike.id, (0, 0, 0), 1, 0, FOE, RULES)
        await live.resolve_round(db, record, RULES)
    assert record.finished, "the rat is one hit"
    assert await says(db, p.heroes[0], "slot_boss,rank-s,won,no") == "yes"
    assert await quests.completed(db, p.teams[0], "rank-s", 5) == 1


# --- housekeeping ------------------------------------------------------------------------------------------------------

async def test_a_swept_fight_takes_its_quest_links_with_it(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    record = await db.scalar(select(FightRecord))
    await ended(db, record)
    await db.execute(update(FightRecord).values(updated_at=wallclock.now().replace(year=2000)))
    assert await fighttags.sweep(db, RULES, wallclock.timestamp()) == 1
    assert await db.scalar(select(func.count()).select_from(QuestBossFight)) == 0


async def test_a_deleted_team_takes_its_boss_fight_links_with_it(db):
    p = await a_world(db, RAT_KING)
    await took(db, p.teams[0])
    await says(db, p.heroes[0], "slot_boss,rank-s,fight,no")
    await rotation.forget_team(db, p.teams[0])
    assert await db.scalar(select(func.count()).select_from(QuestBossFight)) == 0
