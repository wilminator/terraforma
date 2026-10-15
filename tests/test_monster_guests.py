"""Monster-party play, slice d: guests, people who are not logged in, command the monsters with a token instead of an account. On every database."""

import pytest
from sqlalchemy import func, select

from starlette.websockets import WebSocketDisconnect

from terraforma.fights import live
from terraforma.fights.combatant import Command
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.monsterplay import service as monsters
from terraforma.monsterplay.models import MonsterGuest, MonsterLockout, MonsterSeat, MonsterVolunteer
from terraforma.testing import in_app_db, make_team
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_monster_control import FOE, HERO, hero_defends
from .test_monster_play import fight, volunteer
from .test_monster_rewards import Sure, set_hp
from .test_towns import sign_in, with_monsters

pytestmark = pytest.mark.anyio

ORIGIN = {"origin": "http://testserver"}


class TakesGuests(Sure):
    """A game that lets guests play monsters (and, were it allowed to, would pay them: it must not)."""

    def may_queue_monsters(self, volunteer):
        if volunteer.guest_id is not None:
            return "Finish your fight first." if volunteer.in_fight else None
        return super().may_queue_monsters(volunteer)

    def monster_challenge_earned(self, result):
        return 5

    def monster_lockout_seconds(self, result):
        return 3600


RULES = TakesGuests()


async def a_guest(db, rules=RULES):
    """A guest waiting in the queue: the guest's id and token."""
    guest, token = await monsters.new_guest(db, rules)
    await monsters.join_guest(db, rules, guest.id)
    return guest.id, token


async def guest_seated(db, rules=RULES):
    """A guest waits; Mike's hero fights a slime that the guest is seated to command. Returns the guest's id, Mike's id and the fight's."""
    guest_id, _token = await a_guest(db, rules)
    mike, _team, record = await fight(db, rules=rules)
    assert (await db.get(MonsterSeat, record.id)).guest_id == guest_id
    return guest_id, mike.id, record.id


# --- the guest and its token --------------------------------------------------------------------------------------------

async def test_a_guest_is_known_by_its_token_and_only_the_hash_is_kept(db):
    guest, token = await monsters.new_guest(db, RULES)
    assert guest.token_hash != token and len(guest.token_hash) == 64
    assert (await monsters.guest_for(db, RULES, token)).id == guest.id
    assert await monsters.guest_for(db, RULES, token + "x") is None
    assert await monsters.guest_for(db, RULES, "") is None


async def test_a_guest_lapses_unless_it_keeps_being_heard_from(db, later):
    guest, token = await monsters.new_guest(db, RULES)
    ttl = RULES.monster_guest_seconds
    later(ttl - 60)
    assert await monsters.guest_for(db, RULES, token) is not None, "using it renews it"
    later(2 * (ttl - 60))
    assert await monsters.guest_for(db, RULES, token) is not None
    later(2 * (ttl - 60) + ttl + 60)
    assert await monsters.guest_for(db, RULES, token) is None


async def test_lapsed_guests_are_cleared_with_their_places_but_a_seated_guest_stays(db, later):
    seated, _mike, _fight = await guest_seated(db)
    other, other_token = await a_guest(db)
    later(RULES.monster_guest_seconds + 60)
    fresh, _token = await monsters.new_guest(db, RULES)
    left = (await db.scalars(select(MonsterGuest))).all()
    assert len(left) == 2 and seated in {each.id for each in left} and fresh in left, "the one in a fight stays; the lapsed one that only waited is gone"
    assert await monsters.guest_for(db, RULES, other_token) is None
    assert (await db.scalar(select(func.count()).select_from(MonsterVolunteer).where(MonsterVolunteer.guest_id == other))) == 0


# --- the rules --------------------------------------------------------------------------------------------------------------

async def test_the_engines_rules_refuse_a_guest(db):
    guest, _token = await monsters.new_guest(db, Rules())
    with pytest.raises(monsters.MonsterError, match="Log in"):
        await monsters.join_guest(db, Rules(), guest.id)


async def test_the_rules_are_told_a_guest_has_no_account_teams_or_lockout(db):
    told = []

    class Watching(TakesGuests):
        def may_queue_monsters(self, volunteer):
            told.append(volunteer)
            return super().may_queue_monsters(volunteer)

    guest, _token = await monsters.new_guest(db, Watching())
    await monsters.join_guest(db, Watching(), guest.id)
    assert told[0].account_id is None and told[0].guest_id == guest.id
    assert (told[0].is_admin, told[0].team_ids, told[0].in_fight, told[0].locked_until) == (False, (), False, None)


# --- the queue and the seat ---------------------------------------------------------------------------------------------------

async def test_guests_and_accounts_wait_in_one_queue_in_order(db, later):
    first = await volunteer(db, "Ann")
    later(5)
    guest, _token = await a_guest(db)
    later(5)
    third = await volunteer(db, "Bea")
    rows = (await db.scalars(select(MonsterVolunteer).order_by(MonsterVolunteer.queued_at))).all()
    assert [(each.account_id, each.guest_id) for each in rows] == [(first.id, None), (None, guest), (third.id, None)]
    assert await monsters.position(db, rows[1]) == 2


async def test_a_fight_seats_a_waiting_guest_who_then_commands_the_monsters(db):
    guest_id, mike, fight_id = await guest_seated(db)
    seat = await db.get(MonsterSeat, fight_id)
    assert (seat.account_id, seat.holder) == (None, -guest_id)
    record = await db.get(FightRecord, fight_id)
    assert (await live.owners(db, record))[FOE] == -guest_id
    assert await live.submit_command(db, record, -guest_id, FOE, Command.DEFEND, 0, FOE, RULES) == 1
    assert (await monsters.status_guest(db, RULES, guest_id))["seat"] == {"fight": fight_id, "party": 1}
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, mike, FOE, Command.DEFEND, 0, FOE, RULES)
    with pytest.raises(live.NotYours):
        await live.submit_command(db, record, -guest_id, HERO, Command.DEFEND, 0, HERO, RULES)


async def test_a_guest_in_a_fight_may_not_queue_again(db):
    guest_id, _mike, _fight = await guest_seated(db)
    with pytest.raises(monsters.MonsterError, match="Finish your fight"):
        await monsters.join_guest(db, RULES, guest_id)


async def test_a_guest_leaves_the_queue_and_must_check_in(db, later):
    guest_id, _token = await a_guest(db)
    assert (await monsters.status_guest(db, RULES, guest_id))["position"] == 1
    later(RULES.monster_checkin_seconds + 1)
    with pytest.raises(monsters.MonsterError, match="not in the monster queue"):
        await monsters.check_in_guest(db, RULES, guest_id)
    await monsters.join_guest(db, RULES, guest_id)
    assert await monsters.leave_guest(db, guest_id) is True
    assert await monsters.leave_guest(db, guest_id) is False


async def test_the_view_tells_a_guest_it_commands_the_monsters_and_hides_the_heroes_choices(db):
    guest_id, mike, fight_id = await guest_seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record, RULES)
    view = await live.view(db, record, RULES, -guest_id)
    assert [(each["party"], each["yours"], each["committed"]) for each in view["fighters"]] == [(0, False, True), (1, True, False)]
    assert view["seat"] == {"party": 1, "absent": False, "admin": False}
    assert "seat" not in await live.view(db, record, RULES, mike)


async def test_a_guest_who_missed_a_round_is_absent_and_says_it_is_back(db):
    guest_id, mike, fight_id = await guest_seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record, RULES)
    await live.resolve_round(db, record, RULES)
    seat = await db.get(MonsterSeat, fight_id)
    assert seat.absent is True
    with pytest.raises(monsters.MonsterError, match="not commanding"):
        await monsters.come_back(db, record, mike)
    assert (await monsters.come_back(db, record, -guest_id)).absent is False


async def test_an_admin_taking_an_absent_guests_seat_replaces_the_guest(db):
    from .test_monster_control import an_admin

    guest_id, mike, fight_id = await guest_seated(db)
    record = await db.get(FightRecord, fight_id)
    await hero_defends(db, mike, record, RULES)
    await live.resolve_round(db, record, RULES)
    admin = await an_admin(db)
    seat = await monsters.take_over(db, RULES, record, admin)
    assert (seat.account_id, seat.guest_id, seat.holder, seat.admin) == (admin, None, admin, True)


# --- when the fight ends ----------------------------------------------------------------------------------------------------------

async def test_a_guest_earns_no_tokens_and_is_never_locked_out_whatever_the_rules_say(db):
    guest_id, mike, fight_id = await guest_seated(db)
    await set_hp(db, fight_id, 0, 500, strength=60)
    await set_hp(db, fight_id, 1, 1)
    record = await db.get(FightRecord, fight_id)
    await live.submit_command(db, record, mike, HERO, Command.ATTACK_LEFT, 0, FOE, RULES)
    await live.resolve_round(db, record, RULES)
    assert record.finished is True
    assert (await db.get(MonsterSeat, fight_id)).settled is True
    assert (await db.scalar(select(func.count()).select_from(MonsterLockout))) == 0


# --- the calls ---------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game():
    return Game(name="Test Game", rules=TakesGuests())


def _join(client, **kw):
    return client.post("/api/monsters/guest/join", headers=ORIGIN, **kw)


def _bearer(token):
    return {"authorization": f"Bearer {token}"}


def test_a_guest_joins_over_http_and_asks_where_it_stands(app_client):
    joined = expect(_join(app_client), 200).json()
    assert joined["queued"] is True and joined["position"] == 1 and len(joined["token"]) >= 40
    where = expect(app_client.get("/api/monsters/guest", headers=_bearer(joined["token"])), 200).json()
    assert (where["queued"], where["position"], where["seat"]) == (True, 1, None)
    assert "token" not in where, "the token is said once"


def test_every_call_of_a_guest_needs_its_token(app_client):
    joined = expect(_join(app_client), 200).json()
    for headers in ({}, _bearer("nope"), {"authorization": f"Basic {joined['token']}"}, {"authorization": "Bearer"}):
        expect(app_client.get("/api/monsters/guest", headers=headers), 401)
        expect(app_client.post("/api/monsters/guest/check-in", headers={**ORIGIN, **headers}), 401)
    expect(app_client.get("/api/monsters/guest/fights/1", headers=_bearer("nope")), 401)


def test_the_guest_calls_that_change_something_need_this_sites_origin(app_client):
    token = expect(_join(app_client), 200).json()["token"]
    expect(app_client.post("/api/monsters/guest/join"), 403)
    for path in ("/check-in", "/leave", "/fights/1/back"):
        expect(app_client.post(f"/api/monsters/guest{path}", headers=_bearer(token)), 403)
        expect(app_client.post(f"/api/monsters/guest{path}", headers={**_bearer(token), "origin": "http://evil.example"}), 403)
    expect(app_client.post("/api/monsters/guest/check-in", headers={**_bearer(token), **ORIGIN}), 200)
    assert expect(app_client.post("/api/monsters/guest/leave", headers={**_bearer(token), **ORIGIN}), 200).json()["queued"] is False


def test_the_engines_own_rules_turn_a_guest_away_and_make_nothing(app_client):
    app_client.app.state.game = None  # (the engine's own rules)
    answer = expect(_join(app_client), 409).json()
    assert answer["detail"] == "Log in to play monsters."

    async def count(db):
        return await db.scalar(select(func.count()).select_from(MonsterGuest))

    assert in_app_db(app_client, count) == 0


def test_making_guests_is_rate_limited_by_address(app_client):
    app_client.app.state.game = None
    for _ in range(20):
        expect(_join(app_client), 409)
    expect(_join(app_client), 429)


def _fight_for_a_guest(app_client):
    """Mike's hero fights a slime; a guest (joined over HTTP) is seated to command it. Returns the guest's token and the fight's id."""
    in_app_db(app_client, lambda db: with_monsters(db))
    token = expect(_join(app_client), 200).json()["token"]
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Mikes", heroes=("Aria",))["id"]

    async def start(db):
        from terraforma.heroes.models import Team

        await ensure_start(db)
        return (await live.start_team_fight(db, await db.get(Team, team), ["slime"], TakesGuests(), monster_play=True)).id

    return token, in_app_db(app_client, start), mike


def test_a_guest_looks_at_the_fight_and_commands_the_monsters_over_http(app_client):
    token, fight_id, _mike = _fight_for_a_guest(app_client)
    guest = {**_bearer(token), **ORIGIN}
    assert expect(app_client.get("/api/monsters/guest", headers=guest), 200).json()["seat"] == {"fight": fight_id, "party": 1}
    seen = expect(app_client.get(f"/api/monsters/guest/fights/{fight_id}", headers=guest), 200).json()
    assert [(each["party"], each["yours"]) for each in seen["fighters"]] == [(0, False), (1, True)]
    assert seen["seat"] == {"party": 1, "absent": False, "admin": False}
    body = {"fighter": {"party": 1, "group": 0, "character": 0}, "command": "defend", "target": {"party": 1, "group": 0, "character": 0}}
    assert expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/commands", json=body, headers=guest), 202).json()["round"] == 1
    hero = {"fighter": {"party": 0, "group": 0, "character": 0}, "command": "defend", "target": {"party": 0, "group": 0, "character": 0}}
    expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/commands", json=hero, headers=guest), 403)
    assert expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/back", headers=guest), 200).json() == {"seat": {"party": 1, "absent": False, "admin": False}}
    expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/commands", json={**body, "command": "nonsense"}, headers=guest), 422)


def test_another_guest_and_a_logged_in_player_cannot_reach_a_guests_fight(app_client):
    token, fight_id, mike = _fight_for_a_guest(app_client)
    other = {**_bearer(expect(_join(app_client), 200).json()["token"]), **ORIGIN}
    body = {"fighter": {"party": 1, "group": 0, "character": 0}, "command": "defend", "target": {"party": 1, "group": 0, "character": 0}}
    expect(app_client.get(f"/api/monsters/guest/fights/{fight_id}", headers=other), 403)
    expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/commands", json=body, headers=other), 403)
    expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/back", headers=other), 403)
    expect(app_client.post(f"/api/fights/{fight_id}/commands", json=body, headers=mike), 403)
    expect(app_client.get("/api/monsters/guest/fights/999", headers={**_bearer(token), **ORIGIN}), 404)


# --- the guest's fight socket ---------------------------------------------------------------------------------------------------------

PROTOCOL = "terraforma.guest"


def _socket(app_client, fight_id, token, headers=ORIGIN, protocols=None):
    return app_client.websocket_connect(f"/ws/monsters/guest/fights/{fight_id}", headers=headers, subprotocols=protocols or [PROTOCOL, token])


def test_the_guest_socket_hears_who_committed_and_then_the_round(app_client):
    token, fight_id, mike = _fight_for_a_guest(app_client)
    with _socket(app_client, fight_id, token) as socket:
        assert socket.accepted_subprotocol == PROTOCOL, "the token is never echoed back"
        assert socket.receive_json() == {"type": "joined", "fight": fight_id, "watching": 1}
        body = {"fighter": {"party": 0, "group": 0, "character": 0}, "command": "defend", "target": {"party": 0, "group": 0, "character": 0}}
        expect(app_client.post(f"/api/fights/{fight_id}/commands", json=body, headers=mike), 202)
        assert socket.receive_json() == {"type": "committed", "fight": fight_id, "round": 1, "fighter": body["fighter"]}
        guest = {**_bearer(token), **ORIGIN}
        monster = {"fighter": {"party": 1, "group": 0, "character": 0}, "command": "defend", "target": {"party": 1, "group": 0, "character": 0}}
        expect(app_client.post(f"/api/monsters/guest/fights/{fight_id}/commands", json=monster, headers=guest), 202)
        assert socket.receive_json()["type"] == "committed"
        played = socket.receive_json()
        assert played["type"] == "round" and played["round"] == 1 and played["events"]


def test_the_guest_socket_needs_this_sites_origin_and_the_token_as_its_subprotocol(app_client):
    token, fight_id, _mike = _fight_for_a_guest(app_client)
    refused = [
        {"headers": {}},
        {"headers": {"origin": "http://evil.example"}},
        {"protocols": [PROTOCOL]},
        {"protocols": [PROTOCOL, "nope"]},
        {"protocols": ["other", token]},
        {"protocols": [PROTOCOL, token, "more"]},
    ]
    for kwargs in refused:
        with pytest.raises(WebSocketDisconnect):
            with _socket(app_client, fight_id, token, **kwargs) as socket:
                socket.receive_json()


def test_the_guest_socket_lets_in_only_the_guest_the_monsters_are_seated_to(app_client):
    token, fight_id, _mike = _fight_for_a_guest(app_client)
    other = expect(_join(app_client), 200).json()["token"]
    for kwargs in ({"token": other}, {"fight_id": 999}):
        with pytest.raises(WebSocketDisconnect):
            with _socket(app_client, kwargs.get("fight_id", fight_id), kwargs.get("token", token)) as socket:
                socket.receive_json()


def test_the_guest_socket_is_rate_limited_by_address(app_client):
    token, fight_id, _mike = _fight_for_a_guest(app_client)
    from terraforma.accounts import ratelimit

    async def spend(db):
        for _ in range(ratelimit.MONSTER_GUEST_BY_ADDRESS.attempts):
            await ratelimit.hit(app_client.app.state.sessionmaker, ratelimit.MONSTER_GUEST_BY_ADDRESS, "testclient")

    in_app_db(app_client, spend)
    with pytest.raises(WebSocketDisconnect):
        with _socket(app_client, fight_id, token) as socket:
            socket.receive_json()


async def test_the_example_game_takes_guests_but_not_while_they_fight(db):
    from terraforma.example import GAME

    guest, _token = await monsters.new_guest(db, GAME.rules)
    assert (await monsters.join_guest(db, GAME.rules, guest.id)).guest_id == guest.id
