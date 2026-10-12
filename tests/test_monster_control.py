"""Monster-party play, slice b: a seated volunteer commands the monsters, rounds wait for them, absence hands the monsters
to the AI, an admin may sit in a running fight, and monsters do not flee. On every database."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.challenge import service as challenge
from terraforma.fights import live
from terraforma.fights.combatant import Command
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.heroes.models import Hero, Team
from terraforma.monsterplay import service as monsters
from terraforma.monsterplay.models import MonsterSeat
from terraforma.models import Account
from terraforma.testing import in_app_db, make_team
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_monster_play import RULES, fight, volunteer
from .test_towns import PASSWORD, account, sign_in, with_monsters

pytestmark = pytest.mark.anyio

HERO = (0, 0, 0)
FOE = (1, 0, 0)


async def seated(db, **kw):
    """Ann waits in the queue; Mike's hero fights a slime that Ann is seated to command. Returns the ids: Mike, Ann and the fight."""
    ann = await volunteer(db, "Ann")
    mike, _team, record = await fight(db, **kw)
    ids = (mike.id, ann.id, record.id)
    assert (await db.get(MonsterSeat, ids[2])).account_id == ids[1]
    return ids


async def hero_defends(db, mike, record, rules=RULES):
    return await live.submit_command(db, record, mike, HERO, Command.DEFEND, 0, HERO, rules)


async def monster_attacks(db, ann, record, rules=RULES, command=Command.ATTACK_LEFT):
    return await live.submit_command(db, record, ann, FOE, command, 0, HERO, rules)


async def played_commands(db, fight_id, sequence):
    row = await db.get(FightActionRecord, (fight_id, sequence))
    return {tuple(each["address"]): each for each in row.commands}


# --- who commands whom ---------------------------------------------------------------------------------------------

async def test_the_seated_volunteer_commands_the_monsters_and_the_heroes_player_the_heroes(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    assert await monster_attacks(db, ann, record) == 1
    assert await hero_defends(db, mike, record) == 1
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, ann, HERO, Command.DEFEND, 0, HERO, RULES)
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, mike, FOE, Command.DEFEND, 0, FOE, RULES)


async def test_a_stranger_commands_nobody(db):
    _mike, _ann, fight_id = await seated(db)
    other = (await account(db, "Cyril")).id
    record = await db.get(FightRecord, fight_id)
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, other, FOE, Command.DEFEND, 0, FOE, RULES)


async def test_with_nobody_seated_the_monsters_belong_to_the_ai(db):
    mike, _team, record = await fight(db)
    mike_id = mike.id
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, mike_id, FOE, Command.DEFEND, 0, FOE, RULES)


async def test_the_volunteer_is_one_of_the_fights_watchers_and_finds_it_in_their_fights(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    assert await live.watchers(db, record) == {mike, ann}
    mine = await live.mine(db, ann)
    assert [(each["id"], each["heroes"], each["monsters"]) for each in mine] == [(fight_id, [], True)]
    assert [(each["id"], each["monsters"]) for each in await live.mine(db, mike)] == [(fight_id, False)]
    assert [each["id"] for each in await live.mine(db, ann, running_only=True)] == [fight_id]


async def test_the_fight_view_marks_the_monsters_as_the_volunteers_and_never_shows_what_the_heroes_chose(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    view = await live.view(db, record, RULES, ann)
    assert [(each["party"], each["yours"], each["committed"]) for each in view["fighters"]] == [(0, False, True), (1, True, False)]
    assert "command" not in str(view["fighters"]) and view["seat"] == {"party": 1, "absent": False, "admin": False}
    assert "seat" not in await live.view(db, record, RULES, mike)


# --- rounds wait for the volunteer -----------------------------------------------------------------------------------

async def test_the_round_waits_for_the_volunteer_and_plays_what_they_chose(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    assert await live.everyone_committed(db, record, RULES) is False
    await monster_attacks(db, ann, record)
    assert await live.everyone_committed(db, record, RULES) is True
    result = await live.resolve_round(db, record, RULES)
    assert result.round == 1
    commands = await played_commands(db, fight_id, 1)
    assert (commands[FOE]["command"], commands[FOE]["target"]) == (int(Command.ATTACK_LEFT), list(HERO))
    assert (await db.get(MonsterSeat, fight_id)).missed == 0


# --- absence -------------------------------------------------------------------------------------------------------------

async def test_a_round_with_nothing_from_the_volunteer_marks_them_absent_and_counts_it(db):
    mike, _ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)  # (the timer did: the volunteer never committed)
    seat = await db.get(MonsterSeat, fight_id)
    assert (seat.missed, seat.absent) == (1, True)
    assert FOE in await played_commands(db, fight_id, 1), "the AI had no earlier commands to repeat, so it chose"


async def test_a_missed_round_repeats_their_last_commands(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await monster_attacks(db, ann, record, command=Command.DEFEND)
    await live.resolve_round(db, record, RULES)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)
    second = (await played_commands(db, fight_id, 2))[FOE]
    assert (second["command"], (await db.get(MonsterSeat, fight_id)).absent) == (int(Command.DEFEND), True)


async def test_while_absent_the_ai_plays_the_monsters_and_the_round_does_not_wait_for_them(db):
    mike, _ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)
    await hero_defends(db, mike, record)
    assert await live.everyone_committed(db, record, RULES) is True
    await live.resolve_round(db, record, RULES)
    seat = await db.get(MonsterSeat, fight_id)
    assert (seat.missed, seat.absent) == (1, True), "the AI's rounds are not the volunteer's misses"
    assert FOE in await played_commands(db, fight_id, 2)


async def test_commanding_again_brings_an_absent_volunteer_back(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)
    assert (await db.get(MonsterSeat, fight_id)).absent is True
    await monster_attacks(db, ann, record)
    assert (await db.get(MonsterSeat, fight_id)).absent is False
    await hero_defends(db, mike, record)
    assert await live.everyone_committed(db, record, RULES) is True


async def test_saying_they_are_back_brings_an_absent_volunteer_back_and_only_theirs(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)
    with pytest.raises(monsters.MonsterError, match="not commanding"):
        await monsters.come_back(db, record, mike)
    await monsters.come_back(db, record, ann)
    seat = await db.get(MonsterSeat, fight_id)
    assert (seat.absent, seat.missed) == (False, 1), "the missed round stays counted"


async def test_a_game_may_forgive_missed_rounds(db):
    class Patient(Rules):
        def monster_absent(self, missed):
            return missed >= 2

    patient = Patient()
    mike, _ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    for expected in (False, True):
        await hero_defends(db, mike, record, patient)
        await live.resolve_round(db, record, patient)
        assert (await db.get(MonsterSeat, fight_id)).absent is expected


async def test_a_round_where_the_volunteer_gave_some_commands_is_not_a_missed_one(db):
    mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await monster_attacks(db, ann, record)
    await live.resolve_round(db, record, RULES)
    assert (await db.get(MonsterSeat, fight_id)).missed == 0


# --- fleeing ---------------------------------------------------------------------------------------------------------------

async def test_monsters_do_not_flee_unless_the_game_lets_them(db):
    _mike, ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    with pytest.raises(live.Refused, match="no running"):
        await live.submit_command(db, record, ann, FOE, Command.RUN, 0, FOE, RULES)

    class Cowards(Rules):
        def monster_flee_allowed(self, fight, address):
            return True

    await live.submit_command(db, record, ann, FOE, Command.RUN, 0, FOE, Cowards())


async def test_heroes_flee_as_before(db):
    mike, _ann, fight_id = await seated(db)
    record = await db.get(FightRecord, fight_id)
    await live.submit_command(db, record, mike, HERO, Command.RUN, 0, HERO, RULES)


# --- an admin sits in a running fight -----------------------------------------------------------------------------

async def an_admin(db, name="Root"):
    admin = (await account(db, name)).id
    await challenge.set_admin(db, admin)
    return admin


async def test_only_an_admin_may_sit_in_a_running_fight(db):
    mike, _team, record = await fight(db)
    mike_id, fight_id = mike.id, record.id
    with pytest.raises(monsters.MonsterError, match="admin"):
        await monsters.take_over(db, RULES, await db.get(FightRecord, fight_id), mike_id)


async def test_an_admin_takes_the_empty_seat_and_the_round_end_is_pushed_back(db, later):
    _mike, _team, record = await fight(db)
    fight_id = record.id
    admin = await an_admin(db)
    later(25)
    record = await db.get(FightRecord, fight_id)
    soon = live.aware(record.round_deadline)
    seat = await monsters.take_over(db, RULES, record, admin)
    assert (seat.account_id, seat.admin, seat.party, seat.absent) == (admin, True, 1, False)
    assert live.aware(record.round_deadline) == wallclock.now() + timedelta(seconds=RULES.round_length(record.time_multiplier)) > soon
    await live.submit_command(db, record, admin, FOE, Command.DEFEND, 0, FOE, RULES)


async def test_taking_over_does_not_shorten_a_round_that_has_longer_to_run(db):
    _mike, _team, record = await fight(db)
    fight_id = record.id
    admin = await an_admin(db)
    record = await db.get(FightRecord, fight_id)
    record.round_deadline = wallclock.now() + timedelta(hours=1)
    far = live.aware(record.round_deadline)
    await monsters.take_over(db, RULES, record, admin)
    assert live.aware(record.round_deadline) == far


async def test_an_admin_cannot_take_the_seat_of_a_volunteer_who_is_playing(db):
    _mike, _ann, fight_id = await seated(db)
    admin = await an_admin(db)
    with pytest.raises(monsters.MonsterError, match="player is commanding"):
        await monsters.take_over(db, RULES, await db.get(FightRecord, fight_id), admin)


async def test_an_admin_replaces_an_absent_volunteer(db):
    mike, _ann, fight_id = await seated(db)
    admin = await an_admin(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record)
    await live.resolve_round(db, record, RULES)
    seat = await monsters.take_over(db, RULES, record, admin)
    assert (seat.account_id, seat.admin, seat.absent) == (admin, True, False)
    assert (await monsters.seat_of(db, admin)).fight_id == fight_id


async def test_an_admin_with_a_hero_in_the_fight_cannot_command_its_monsters(db):
    _mike, _team, record = await fight(db)
    fight_id = record.id
    hero_account = await db.scalar(select(Hero.account_id).join(FightParticipant, FightParticipant.hero_id == Hero.id).where(FightParticipant.fight_id == fight_id))
    await challenge.set_admin(db, hero_account)
    with pytest.raises(monsters.MonsterError, match="hero"):
        await monsters.take_over(db, RULES, await db.get(FightRecord, fight_id), hero_account)


async def test_nobody_sits_in_a_fight_that_is_over_or_that_does_not_allow_it(db):
    _mike, _team, record = await fight(db, monster_play=False)
    fight_id = record.id
    admin = await an_admin(db)
    with pytest.raises(monsters.MonsterError, match="monster"):
        await monsters.take_over(db, RULES, await db.get(FightRecord, fight_id), admin)
    _m, _t, other = await fight(db, "Zed")
    other_id = other.id
    record = await db.get(FightRecord, other_id)
    record.finished = True
    await db.flush()
    with pytest.raises(monsters.MonsterError, match="over"):
        await monsters.take_over(db, RULES, record, admin)


# --- the calls -------------------------------------------------------------------------------------------------------------

def _setup_http(app_client):
    """Mike (heroes) and Ann (the volunteer) have an account and a team each; Ann waits in the queue; a fight starts, and Ann is seated."""
    in_app_db(app_client, lambda db: with_monsters(db))
    ann = sign_in(app_client, "Ann")
    make_team(app_client, ann, "Anns", heroes=("Anna",))
    expect(app_client.post("/api/monsters/queue/join", headers=ann), 200)
    mike = sign_in(app_client, "Mike")  # (now logged in as Mike)
    team = make_team(app_client, mike, "Mikes", heroes=("Aria",))["id"]

    async def start(db):
        await ensure_start(db)
        return (await live.start_team_fight(db, await db.get(Team, team), ["slime"], RULES, monster_play=True)).id

    return ann, mike, in_app_db(app_client, start)


def test_the_volunteer_looks_at_the_fight_and_commands_the_monsters_over_http(app_client):
    _ann, _mike, fight_id = _setup_http(app_client)
    ann = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Ann", "password": PASSWORD}).json()["csrf_token"]}  # (Mike was logged in last)
    seen = expect(app_client.get(f"/api/fights/{fight_id}"), 200).json()
    assert [(each["party"], each["yours"]) for each in seen["fighters"]] == [(0, False), (1, True)]
    body = {"fighter": {"party": 1, "group": 0, "character": 0}, "command": "defend", "target": {"party": 1, "group": 0, "character": 0}}
    assert expect(app_client.post(f"/api/fights/{fight_id}/commands", json=body, headers=ann), 202).json()["round"] == 1
    hero = {"fighter": {"party": 0, "group": 0, "character": 0}, "command": "defend", "target": {"party": 0, "group": 0, "character": 0}}
    assert expect(app_client.post(f"/api/fights/{fight_id}/commands", json=hero, headers=ann), 403).json()
    listed = expect(app_client.get("/api/fights"), 200).json()
    assert [(each["id"], each["monsters"]) for each in listed] == [(fight_id, True)]
    assert expect(app_client.post(f"/api/fights/{fight_id}/monsters/back", headers=ann), 200).json() == {"seat": {"party": 1, "absent": False, "admin": False}}


def test_an_admin_takes_over_a_running_fight_over_http_and_nobody_else_may(app_client):
    _ann, mike, fight_id = _setup_http(app_client)
    base = f"/api/fights/{fight_id}/monsters/take-over"
    assert app_client.post(base).status_code == 403, "no CSRF token"
    # Ann holds the seat and is playing: even an admin has to wait for her to go absent.
    root = sign_in(app_client, "Root")
    in_app_db(app_client, lambda db: _make_admin(db, "Root"))
    assert expect(app_client.post(base, headers=root), 409).json()["detail"] == "a player is commanding the monsters"
    expect(app_client.post(f"/api/fights/{fight_id}/monsters/take-over", headers=mike), 403)


async def _make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))
