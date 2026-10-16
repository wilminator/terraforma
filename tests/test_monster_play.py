"""Monster-party play, slice a: the volunteer queue, lockouts, and seating a volunteer when a fight is created, on every database."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.challenge import service as challenge
from terraforma.fights import live
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.heroes import service
from terraforma.monsterplay import service as monsters
from terraforma.monsterplay.facts import Seating, Volunteer
from terraforma.monsterplay.models import MonsterLockout, MonsterSeat, MonsterVolunteer
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.service import apply as apply_relation
from terraforma.testing import in_app_db, make_team
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_joining import a_team, scene
from .test_towns import PASSWORD, account, sign_in, with_monsters
from .test_walking import RULES as WALK_RULES
from .test_walking import party_on, seeded, walk_grass  # noqa: F401 (seeded is a fixture)
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns

pytestmark = [pytest.mark.anyio, pytest.mark.fights]

RULES = Rules()


async def volunteer(db, name, team=True):
    """An account named $name, with a team of one hero (unless $team is False), waiting in the queue."""
    owner = await account(db, name)
    if team:
        await a_team(db, owner, f"{name} team", f"{name} hero")
    await monsters.join(db, RULES, owner.id)
    return SimpleNamespace(id=owner.id)  # (loading content later expires the account: keep only its id)


async def fight(db, owner_name="Mike", monster_play=True, relations=None, rules=None):
    """A fight between a team of $owner_name's and a slime, started now."""
    await with_monsters(db)
    await ensure_start(db)
    owner = await account(db, owner_name)
    team = await a_team(db, owner, f"{owner_name} heroes", f"{owner_name} hero")
    record = await live.start_team_fight(db, team, ["slime"], rules or RULES, monster_play=monster_play, relations=relations)
    return owner, team, record


async def queue(db):
    return [row.account_id for row in (await db.scalars(select(MonsterVolunteer).order_by(MonsterVolunteer.queued_at, MonsterVolunteer.account_id))).all()]


# --- the queue ---------------------------------------------------------------------------------------------------------

async def test_an_account_with_a_team_joins_the_queue_and_waits_in_order(db, later):
    first = await volunteer(db, "Ann")
    later(5)
    second = await volunteer(db, "Bea")
    assert await queue(db) == [first.id, second.id]
    assert [(await monsters.status(db, RULES, who.id))["position"] for who in (first, second)] == [1, 2]


async def test_joining_again_keeps_the_place_and_checks_in(db, later):
    first = await volunteer(db, "Ann")
    later(5)
    second = await volunteer(db, "Bea")
    later(30)
    await monsters.join(db, RULES, first.id)
    assert await queue(db) == [first.id, second.id]
    assert monsters._aware((await monsters.queued(db, first.id)).checked_in) == wallclock.now()


async def test_an_account_with_no_team_is_refused_with_the_reason(db):
    owner = await account(db, "Ann")
    with pytest.raises(monsters.MonsterError, match="Make a team"):
        await monsters.join(db, RULES, owner.id)
    assert await queue(db) == []


async def test_leaving_takes_the_place_back_and_says_whether_there_was_one(db):
    ann = await volunteer(db, "Ann")
    assert await monsters.leave(db, ann.id) is True
    assert await monsters.leave(db, ann.id) is False
    assert await queue(db) == []


async def test_checking_in_needs_a_place_and_one_that_went_stale_is_lost(db, later):
    ann = await volunteer(db, "Ann")
    later(RULES.monster_checkin_seconds - 1)
    await monsters.check_in(db, RULES, ann.id)
    later(2 * RULES.monster_checkin_seconds)
    with pytest.raises(monsters.MonsterError, match="not in the monster queue"):
        await monsters.check_in(db, RULES, ann.id)
    assert (await monsters.status(db, RULES, ann.id))["queued"] is False
    with pytest.raises(monsters.MonsterError):
        await monsters.check_in(db, RULES, (await account(db, "Nobody")).id)


async def test_a_volunteer_who_went_away_waits_again_from_the_back_when_they_return(db, later):
    ann = await volunteer(db, "Ann")
    later(RULES.monster_checkin_seconds + 1)
    bea = await volunteer(db, "Bea")
    later(RULES.monster_checkin_seconds + 2)
    await monsters.join(db, RULES, ann.id)
    assert await queue(db) == [bea.id, ann.id]


async def test_a_hero_in_a_fight_cannot_volunteer_and_neither_can_a_monster_player(db):
    owner, _team, record = await fight(db, monster_play=False)
    owner_id, record_id = owner.id, record.id
    with pytest.raises(monsters.MonsterError, match="Finish your fight"):
        await monsters.join(db, RULES, owner_id)
    other = await volunteer(db, "Ann")
    _o, _t, seated = await fight(db, "Zed", monster_play=True)
    assert (await monsters.seat_of(db, other.id)).fight_id == seated.id
    with pytest.raises(monsters.MonsterError, match="Finish your fight"):
        await monsters.join(db, RULES, other.id)
    seated.finished = True
    await db.flush()
    await monsters.join(db, RULES, other.id)  # the fight is over: they may volunteer again
    assert (await db.get(FightRecord, record_id)).finished is False


async def test_a_lockout_keeps_an_account_out_until_it_ends_but_not_an_admin(db, later):
    ann = await volunteer(db, "Ann")
    await monsters.leave(db, ann.id)
    db.add(MonsterLockout(account_id=ann.id, until=wallclock.now() + timedelta(hours=1)))
    await db.flush()
    with pytest.raises(monsters.MonsterError, match="locked out"):
        await monsters.join(db, RULES, ann.id)
    assert (await monsters.status(db, RULES, ann.id))["locked_until"] is not None
    await challenge.set_admin(db, ann.id)
    await monsters.join(db, RULES, ann.id)
    await monsters.leave(db, ann.id)
    await challenge.set_admin(db, ann.id, False)
    later(3601)
    await monsters.join(db, RULES, ann.id)
    assert (await monsters.status(db, RULES, ann.id))["locked_until"] is None


# --- the hooks ---------------------------------------------------------------------------------------------------------

def test_the_default_hooks_judge_a_volunteer_by_what_they_are_told():
    assert RULES.may_queue_monsters(Volunteer(None)) == "Log in to play monsters."
    assert RULES.may_queue_monsters(Volunteer(1, team_ids=(3,))) is None
    assert RULES.may_play_monsters(Volunteer(1, team_ids=(3,)), Seating(7)) is None
    assert RULES.may_play_monsters(Volunteer(1), Seating(7, has_hero=True))
    assert RULES.may_play_monsters(Volunteer(1), Seating(7, allied=True))
    assert RULES.may_play_monsters(Volunteer(1, in_fight=True), Seating(7))


async def test_a_game_decides_who_may_queue_even_someone_not_logged_in(db):
    class Open(Rules):
        def may_queue_monsters(self, volunteer):
            return None if volunteer.account_id is None else super().may_queue_monsters(volunteer)

    assert Open().may_queue_monsters(Volunteer(None)) is None
    assert Open().may_queue_monsters(Volunteer(1)) == "Make a team to play monsters."


# --- seating -----------------------------------------------------------------------------------------------------------

async def test_the_longest_waiting_volunteer_is_seated_when_a_fight_is_created(db, later):
    first = await volunteer(db, "Ann")
    later(5)
    second = await volunteer(db, "Bea")
    _owner, _team, record = await fight(db)
    seat = await db.get(MonsterSeat, record.id)
    assert (seat.account_id, seat.party, monsters._aware(seat.seated_at)) == (first.id, 1, wallclock.now())
    assert await queue(db) == [second.id], "the seated volunteer has left the queue"
    assert (await monsters.status(db, RULES, first.id))["seat"] == {"fight": record.id, "party": 1}
    view = await live.view(db, record, RULES)
    assert view["monster_play"] is True and view["monsters_played"] is True


async def test_a_fight_that_does_not_allow_monster_play_seats_nobody(db):
    ann = await volunteer(db, "Ann")
    _owner, _team, record = await fight(db, monster_play=False)
    assert await db.get(MonsterSeat, record.id) is None and await queue(db) == [ann.id]
    view = await live.view(db, record, RULES)
    assert view["monster_play"] is False and view["monsters_played"] is False


async def test_with_nobody_queued_the_ai_plays_as_before(db):
    _owner, _team, record = await fight(db)
    assert record.monster_play is True and await db.get(MonsterSeat, record.id) is None
    assert (await live.view(db, record, RULES))["monsters_played"] is False


async def test_a_volunteer_is_never_seated_into_a_running_fight(db):
    _owner, _team, record = await fight(db)
    ann = await volunteer(db, "Ann")
    assert await monsters.seat(db, RULES, Relations(), record) is not None, "(only a fight being created asks)"
    assert (await monsters.seat_of(db, ann.id)).fight_id == record.id
    # ...and joining the queue after the fight began seated nobody by itself:
    bea = await volunteer(db, "Bea")
    assert await queue(db) == [bea.id]


async def test_an_account_with_a_hero_in_the_fight_stays_queued_and_the_next_is_seated(db, later):
    await with_monsters(db)
    mike = await account(db, "Mike")
    await a_team(db, mike, "Spare", "Spare hero")
    await monsters.join(db, RULES, mike.id)  # (queued first, then their other team's heroes meet monsters)
    later(5)
    ann = await volunteer(db, "Ann")
    team = await service.create_team(db, mike, "Frontline")
    await service.add_to_team(db, mike, team.id, (await service.create_hero(db, mike, "Aria", "fighter")).id)
    record = await live.start_team_fight(db, team, ["slime"], RULES, monster_play=True)
    assert (await db.get(MonsterSeat, record.id)).account_id == ann.id
    assert await queue(db) == [mike.id], "refused for this fight, still waiting for the next"


async def test_an_account_with_a_team_allied_to_the_heroes_is_refused_across_all_its_teams(db):
    await with_monsters(db)
    mike, ann = await account(db, "Mike"), await account(db, "Ann")
    heroes = await a_team(db, mike, "Heroes", "Aria")
    await a_team(db, ann, "Alpha", "Ann one")
    pals = await a_team(db, ann, "Pals", "Ann two")
    await monsters.join(db, RULES, ann.id)
    await apply_relation(db, Relations(), Change(Ref("team", pals.id), Ref("team", heroes.id), score=50, by="game"))
    record = await live.start_team_fight(db, heroes, ["slime"], RULES, monster_play=True)
    assert await db.get(MonsterSeat, record.id) is None and await queue(db) == [ann.id]
    assert (await live.view(db, record, RULES))["monsters_played"] is False


async def test_a_dislike_or_a_stranger_does_not_keep_a_volunteer_out(db):
    await with_monsters(db)
    mike, ann = await account(db, "Mike"), await account(db, "Ann")
    heroes = await a_team(db, mike, "Heroes", "Aria")
    foes = await a_team(db, ann, "Foes", "Ann one")
    await monsters.join(db, RULES, ann.id)
    await apply_relation(db, Relations(), Change(Ref("team", foes.id), Ref("team", heroes.id), score=-50, by="game"))
    record = await live.start_team_fight(db, heroes, ["slime"], RULES, monster_play=True)
    assert (await db.get(MonsterSeat, record.id)).account_id == ann.id


async def test_the_games_relations_judge_who_counts_as_an_ally(db):
    class Everyone(Relations):
        def stance(self, score):
            return "ally"

    await with_monsters(db)
    mike, ann = await account(db, "Mike"), await account(db, "Ann")
    heroes = await a_team(db, mike, "Heroes", "Aria")
    await a_team(db, ann, "Alpha", "Ann one")
    await monsters.join(db, RULES, ann.id)
    record = await live.start_team_fight(db, heroes, ["slime"], RULES, monster_play=True, relations=Everyone())
    assert await db.get(MonsterSeat, record.id) is None


async def test_the_games_rules_can_refuse_a_seat(db):
    class Nobody(Rules):
        def may_play_monsters(self, volunteer, seating):
            return "Not today."

    ann = await volunteer(db, "Ann")
    _owner, _team, record = await fight(db, rules=Nobody())
    assert await db.get(MonsterSeat, record.id) is None and await queue(db) == [ann.id]


async def test_volunteers_who_stopped_checking_in_are_dropped_not_seated(db, later):
    await volunteer(db, "Ann")
    later(RULES.monster_checkin_seconds + 1)
    bea = await volunteer(db, "Bea")
    _owner, _team, record = await fight(db)
    assert (await db.get(MonsterSeat, record.id)).account_id == bea.id and await queue(db) == []


async def test_a_volunteer_seated_in_one_fight_is_not_seated_in_the_next(db):
    ann = await volunteer(db, "Ann")
    _o, _t, first = await fight(db, "Mike")
    first_id = first.id
    _o, _t, second = await fight(db, "Zed")
    assert (await db.get(MonsterSeat, first_id)).account_id == ann.id and await db.get(MonsterSeat, second.id) is None


async def test_leaving_the_queue_before_a_fight_keeps_you_out_of_it(db):
    ann = await volunteer(db, "Ann")
    await monsters.leave(db, ann.id)
    _owner, _team, record = await fight(db)
    assert await db.get(MonsterSeat, record.id) is None


# --- where fights are created ------------------------------------------------------------------------------------------

async def test_leave_town_starts_a_fight_a_volunteer_may_command(db):
    mike, _zed, _host, vanguard, _party = await scene(db)
    ann = await volunteer(db, "Ann")
    result, record = await towns_service.leave(db, Towns(), Rules(), vanguard.id)
    assert result == "reformed" and record.monster_play is True
    assert (await db.get(MonsterSeat, record.id)).account_id == ann.id
    assert mike.id != ann.id


async def test_a_random_encounter_on_the_map_is_a_fight_a_volunteer_may_command(seeded, later):
    ann = await account(seeded, "Ann")
    await a_team(seeded, ann, "Ann team", "Ann hero")
    await monsters.join(seeded, WALK_RULES, ann.id)
    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    hit = (await walk_grass(seeded, owner, party))[-1]
    record = await seeded.get(FightRecord, hit["fight"])
    assert record.monster_play is True and (await seeded.get(MonsterSeat, record.id)).account_id == ann.id


async def test_a_fight_started_with_no_flag_is_not_monster_play(db):
    _owner, _team, record = await fight(db, monster_play=False)
    assert record.monster_play is False


# --- the calls ---------------------------------------------------------------------------------------------------------

def test_the_queue_calls_need_a_login_and_changes_need_the_csrf_token(app_client):
    assert app_client.get("/api/monsters/queue").status_code == 401
    mike = sign_in(app_client, "Mike")
    assert app_client.post("/api/monsters/queue/join").status_code == 403
    assert app_client.post("/api/monsters/queue/leave").status_code == 403
    assert app_client.post("/api/monsters/queue/check-in").status_code == 403
    expect(app_client.get("/api/monsters/queue"), 200)
    assert mike


def test_a_player_volunteers_checks_in_and_leaves(app_client):
    mike = sign_in(app_client, "Mike")
    in_app_db(app_client, lambda db: with_monsters(db))
    answer = expect(app_client.get("/api/monsters/queue"), 200).json()
    assert answer["queued"] is False and "team" in answer["refusal"] and answer["checkin_seconds"] == 60
    assert expect(app_client.post("/api/monsters/queue/join", headers=mike), 409).json()["detail"] == "Make a team to play monsters."
    make_team(app_client, mike)
    joined = expect(app_client.post("/api/monsters/queue/join", headers=mike), 200).json()
    assert (joined["queued"], joined["position"], joined["seat"], joined["refusal"]) == (True, 1, None, None)
    assert expect(app_client.post("/api/monsters/queue/check-in", headers=mike), 200).json()["queued"] is True
    assert expect(app_client.get("/api/monsters/queue"), 200).json()["position"] == 1
    assert expect(app_client.post("/api/monsters/queue/leave", headers=mike), 200).json()["queued"] is False
    assert expect(app_client.post("/api/monsters/queue/check-in", headers=mike), 409).json()["detail"] == "you are not in the monster queue"


def test_the_queue_is_one_place_per_account_and_ordered_across_accounts(app_client):
    in_app_db(app_client, lambda db: with_monsters(db))
    mike = sign_in(app_client, "Mike")
    make_team(app_client, mike)
    expect(app_client.post("/api/monsters/queue/join", headers=mike), 200)
    zed = sign_in(app_client, "Zed")
    make_team(app_client, zed)
    assert expect(app_client.post("/api/monsters/queue/join", headers=zed), 200).json()["position"] in (1, 2)
    queued = in_app_db(app_client, lambda db: queue(db))
    assert len(queued) == 2 and len(set(queued)) == 2


def test_a_seated_player_sees_their_seat(app_client):
    in_app_db(app_client, lambda db: with_monsters(db))
    mike = sign_in(app_client, "Mike")
    make_team(app_client, mike)
    expect(app_client.post("/api/monsters/queue/join", headers=mike), 200)

    async def start(db):
        await with_monsters(db)
        await ensure_start(db)
        zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
        team = await a_team(db, zed, "Zeds", "Zed hero")
        return (await live.start_team_fight(db, team, ["slime"], RULES, monster_play=True)).id

    fight_id = in_app_db(app_client, start)
    seen = expect(app_client.get("/api/monsters/queue"), 200).json()
    assert seen["queued"] is False and seen["seat"] == {"fight": fight_id, "party": 1}
    assert expect(app_client.post("/api/monsters/queue/join", headers=mike), 409).json()["detail"] == "Finish your fight first."
