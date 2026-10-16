"""Live fights: started by the server, commanded by the players' own accounts, played when everyone has committed or the
round's time has run out, with the monsters on the AI, and the result saved at the end. On every database."""

from dataclasses import replace

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.fights import live, specs, store
from terraforma.fights.combatant import Command
from terraforma.fights.models import FightActionRecord, FightCommandRecord, FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.heroes import service
from terraforma.heroes.models import Hero, Team
from terraforma.models import Account

from .test_fight_store import PASSWORD, SEED, a_hero

pytestmark = [pytest.mark.anyio, pytest.mark.fights]

RULES = Rules()
HERO = (0, 0, 0)
FOE = (1, 0, 0)


class Channels:
    """What a fight's watchers would be sent."""

    def __init__(self):
        self.sent = []

    async def push(self, fight_id, message):
        self.sent.append((fight_id, message))


async def a_team(db, foe="rat"):
    hero = await a_hero(db)
    mike = await db.get(Account, hero.account_id)
    team = await service.create_team(db, mike, "Alpha")
    await service.add_to_team(db, mike, team.id, hero.id)
    return hero, mike, team


async def started(db, foe="rat"):
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, [foe], RULES)
    return hero, mike, team, record


async def attack(db, record, mike, rules=RULES):
    return await live.submit_command(db, record, mike.id, HERO, Command.ATTACK_RIGHT, 0, FOE, rules)


# --- starting --------------------------------------------------------------------------------------------------------

async def test_a_team_fight_starts_on_a_clock_with_the_team_against_the_monsters(db):
    hero, _mike, team, record = await started(db, "ogre")
    assert record.finished is False and wallclock.now().timestamp() < live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 30
    rows = (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == record.id).order_by(FightParticipant.party))).all()
    assert [(row.party, row.hero_id, row.monster_key) for row in rows] == [(0, hero.id, None), (1, None, "ogre")]
    fight, played = await store.load_state(db, record, RULES)
    assert played == 0 and fight.parties[0].teams == {team.id: [hero.id]}


async def test_the_games_round_time_sets_the_clock(db):
    class Quick(Rules):
        round_seconds = 5

    _hero, _mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], Quick())
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 5


async def test_a_fight_needs_heroes_monsters_and_free_heroes(db):
    hero, mike, team, _record = await started(db)
    with pytest.raises(live.Refused, match="already in a fight"):
        await live.start_team_fight(db, team, ["rat"], RULES)
    empty = await service.create_team(db, mike, "Empty")
    with pytest.raises(live.Refused, match="no heroes"):
        await live.start_team_fight(db, empty, ["rat"], RULES)
    other = await service.create_team(db, mike, "Other")
    await service.add_to_team(db, mike, other.id, (await service.create_hero(db, mike, "Bram", "fighter")).id)
    for monsters in ([], ["rat"] * 21):
        with pytest.raises(live.Refused, match="between 1 and 20 monsters"):
            await live.start_team_fight(db, other, monsters, RULES)


# --- commands ----------------------------------------------------------------------------------------------------------

async def test_a_player_commands_their_own_fighter_and_may_change_their_mind(db):
    _hero, mike, _team, record = await started(db)
    assert await attack(db, record, mike) == 1
    await live.submit_command(db, record, mike.id, HERO, Command.DEFEND, 0, FOE, RULES)
    rows = (await db.scalars(select(FightCommandRecord).where(FightCommandRecord.fight_id == record.id))).all()
    assert [(row.round_number, row.command, row.target) for row in rows] == [(1, int(Command.DEFEND), [1, 0, 0])]


async def test_a_player_cannot_command_anyone_elses_fighter_or_a_monster(db):
    _hero, mike, _team, record = await started(db)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, zed.id, HERO, Command.ATTACK_RIGHT, 0, FOE, RULES)
    with pytest.raises(live.NotYours, match="isn't yours"):
        await live.submit_command(db, record, mike.id, FOE, Command.ATTACK_LEFT, 0, HERO, RULES)
    with pytest.raises(live.NotFound):
        await live.submit_command(db, record, mike.id, (0, 3, 9), Command.ATTACK_LEFT, 0, FOE, RULES)


async def test_a_command_the_fighter_cannot_do_is_refused(db):
    _hero, mike, _team, record = await started(db)
    with pytest.raises(live.Refused, match="can't do that"):
        await live.submit_command(db, record, mike.id, HERO, Command.ITEM, 99, FOE, RULES)  # no such stack
    with pytest.raises(live.Refused, match="can't do that"):
        await live.submit_command(db, record, mike.id, HERO, Command.SPELL, 5, FOE, RULES)  # no such ability


async def test_everyone_committed_means_every_living_player_fighter(db):
    _hero, mike, _team, record = await started(db)
    assert await live.everyone_committed(db, record, RULES) is False
    await attack(db, record, mike)
    assert await live.everyone_committed(db, record, RULES) is True


# --- rounds --------------------------------------------------------------------------------------------------------------------

async def test_a_round_plays_the_players_command_and_the_monsters_ai_and_starts_the_next_clock(db, later):
    _hero, mike, _team, record = await started(db, "ogre")
    await attack(db, record, mike)
    later(10)
    result = await live.resolve_round(db, record, RULES)
    assert result.round == 1 and result.over is False and result.events
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 30, "a new clock from when it played"
    assert await db.scalar(select(func.count()).select_from(FightCommandRecord)) == 0, "the waiting commands are gone"
    stored = (await store.actions(db, record))[0]
    assert sorted(entry["address"] for entry in stored.commands) == [list(HERO), list(FOE)], "the AI's choice is stored with the round"
    assert await store.verify(db, record, RULES, deep=True) == 1


async def test_a_fighter_who_did_not_commit_defends(db):
    _hero, _mike, _team, record = await started(db, "ogre")
    await live.resolve_round(db, record, RULES)
    stored = (await store.actions(db, record))[0]
    assert [entry["address"] for entry in stored.commands] == [list(FOE)], "only the monster commanded; the hero defended"


async def test_each_call_plays_the_next_round_and_never_one_twice(db):
    _hero, _mike, _team, record = await started(db, "ogre")
    await live.resolve_round(db, record, RULES)
    assert [row.sequence for row in await store.actions(db, record)] == [1]
    record2 = await live.get_record(db, record.id)
    await live.resolve_round(db, record2, RULES)
    assert [row.sequence for row in await store.actions(db, record)] == [1, 2], "each call plays the next round"


# --- the clock ---------------------------------------------------------------------------------------------------------------------

async def test_a_round_plays_when_its_time_has_run_out_and_not_before(db, later):
    _hero, _mike, _team, record = await started(db, "ogre")
    await db.commit()
    assert await live.due(db) == []
    later(29)
    assert await live.due(db) == []
    later(31)
    assert await live.due(db) == [record.id]


async def test_the_timer_plays_overdue_rounds_and_tells_the_watchers(app_client_db, later):
    sessionmaker, record_id = app_client_db
    channels = Channels()
    assert await live.resolve_overdue(sessionmaker, RULES, None, channels) == 0 and channels.sent == []
    later(31)
    assert await live.resolve_overdue(sessionmaker, RULES, None, channels) == 1
    [(fight_id, message)] = channels.sent
    assert fight_id == record_id and message["type"] == "round" and message["round"] == 1 and message["events"]
    assert await live.resolve_overdue(sessionmaker, RULES, None, channels) == 0, "its next clock has not run out"


@pytest.fixture
async def app_client_db(db):
    """A started fight, committed, with a sessionmaker on the same database."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    _hero, _mike, _team, record = await started(db, "ogre")
    await db.commit()
    return async_sessionmaker(db.bind, expire_on_commit=False), record.id


# --- to the end -----------------------------------------------------------------------------------------------------------------------

async def test_a_fight_played_out_saves_the_result_and_pays_the_gold_once(db):
    hero, mike, team, record = await started(db, "rat")
    channels_events = []
    for _ in range(30):
        if record.finished:
            break
        await attack(db, record, mike)
        result = await live.resolve_round(db, record, RULES)
        channels_events.append(result)
    assert record.finished is True and record.round_deadline is None and channels_events[-1].over is True
    assert record.gold_paid is True and (await db.get(Team, team.id)).gold == 12, "the rat's gold, to the team"
    assert (await db.get(Hero, hero.id)).xp > 0, "the result is saved to the hero"
    assert await live.due(db) == [] and await store.verify(db, record, RULES, deep=True) == len(channels_events)
    with pytest.raises(live.Refused, match="over"):
        await attack(db, record, mike)
    with pytest.raises(live.Refused, match="over"):
        await live.resolve_round(db, record, RULES)


async def test_a_view_shows_what_a_page_needs(db):
    hero, mike, _team, record = await started(db, "ogre")
    await attack(db, record, mike)
    seen = await live.view(db, record, RULES, mike.id)
    assert (seen["round"], seen["over"], seen["guid"]) == (1, False, record.guid)
    assert seen["deadline"].endswith("+00:00")
    aria, ogre = seen["fighters"]
    assert (aria["name"], aria["yours"], aria["committed"], aria["alive"]) == ("Aria", True, True, True)
    assert aria["resources"]["HP"] == [40, 40] and (ogre["name"], ogre["yours"], ogre["committed"]) == ("Ogre", False, False)
    stranger = await live.view(db, record, RULES)
    assert not any(each["yours"] for each in stranger["fighters"])
    assert await live.watchers(db, record) == {mike.id}


async def test_a_view_gives_the_callers_fighters_a_menu_of_what_they_can_command(db):
    hero, mike, _team, record = await started(db, "ogre")
    seen = await live.view(db, record, RULES, mike.id)
    aria, ogre = seen["fighters"]
    # (Aria's sword is in her right hand: the left hand is empty, so only the sword is a weapon to choose.)
    assert aria["menu"] == {
        "attacks": [{"command": "attack_right", "hand": "right", "weapon": "Sword", "scope": 0, "aim": "enemy"}],
        "abilities": [{"index": 0, "key": "slash", "name": "Slash", "kind": "skill", "cost": 0, "resource": "MP", "affordable": True, "scope": 0, "aim": "enemy"}],
        "items": [{"index": 1, "key": "potion", "name": "Potion", "quantity": 3, "scope": 0, "aim": "ally"}],
        "can_flee": True,
    }
    assert "menu" not in ogre, "the menu is for the caller's own fighters: the other side's choices are not shown"
    stranger = await live.view(db, record, RULES)
    assert all("menu" not in each for each in stranger["fighters"])


async def test_a_menu_says_what_the_fighter_cannot_do(db):
    hero, mike, _team, record = await started(db, "ogre")
    fight, _played = await store.load_state(db, record, RULES)
    aria = fight.get(HERO)
    aria.current["MP"] = 0
    aria.abilities[0] = replace(aria.abilities[0], mp_cost=3)
    aria.inventory.append([aria.inventory[0][0], 1])  # (the sword again, in a second stack)
    aria.equipment["lhand"] = aria.equipment["rhand"]  # (one weapon in both hands: two-handed, only the left hand swings)

    class NoFleeing(Rules):
        def flee_allowed(self, fight, address):
            return False

    menu = live.menu_of(fight, NoFleeing(), HERO)
    assert [each["command"] for each in menu["attacks"]] == ["attack_left"]
    assert menu["abilities"][0]["cost"] == 3 and menu["abilities"][0]["affordable"] is False
    assert menu["can_flee"] is False


def test_where_a_command_aims_follows_what_it_does():
    aim = lambda **effect: specs.EffectSpec.from_dict(effect).aim  # noqa: E731
    assert aim(effect="hurt", base=3) == "enemy" and aim(effect="cause_bad_status", status="poison") == "enemy"
    assert aim(effect="heal", base=3) == "ally" and aim(effect="cause_good_status", status="haste") == "ally"
    assert aim(effect="revive", base=3) == "downed"
    assert aim(effect="eject", base=50) == "enemy" and aim(effect="eject", base=50, friendly=True) == "ally"
    assert aim(effect="hurt", base=3, targets="all_enemies") == "none", "a scope that names its own targets is not aimed"
    assert aim(effect="heal", base=3, targets="random_party") == "none"
    assert aim(effect="hurt", base=3, targets="group") == "enemy", "a group is chosen by aiming at one of its members"


# --- finding fights --------------------------------------------------------------------------------------------------------------

async def test_an_account_finds_its_fights_running_first_and_with_the_heroes_in_them(db):
    hero, mike, team, record = await started(db, "ogre")
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    assert await live.mine(db, zed.id) == [], "someone with no hero in it sees nothing"
    [found] = await live.mine(db, mike.id)
    assert found["id"] == record.id and found["guid"] == record.guid and found["over"] is False and found["round"] == 1
    assert found["heroes"] == [{"hero_id": hero.id, "name": "Aria"}] and found["deadline"].endswith("+00:00")
    await live.resolve_round(db, record, RULES)
    assert (await live.mine(db, mike.id))[0]["round"] == 2


async def test_running_fights_come_first_then_the_latest_and_a_limit_applies(db):
    hero, mike, team, first = await started(db, "rat")
    await play_to_the_end_live(db, first, mike)
    second = await live.start_team_fight(db, team, ["ogre"], RULES)
    found = await live.mine(db, mike.id)
    assert [each["id"] for each in found] == [second.id, first.id] and [each["over"] for each in found] == [False, True]
    assert [each["id"] for each in await live.mine(db, mike.id, running_only=True)] == [second.id]
    assert [each["id"] for each in await live.mine(db, mike.id, limit=1)] == [second.id]


async def play_to_the_end_live(db, record, mike):
    for _ in range(30):
        if record.finished:
            return
        await attack(db, record, mike)
        await live.resolve_round(db, record, RULES)
    raise AssertionError("the fight went on too long")
