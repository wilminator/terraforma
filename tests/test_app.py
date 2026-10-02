"""The web app: the live fight calls and the fight WebSocket, on every database."""

import asyncio
import json
import time
from contextlib import suppress

import pytest
from starlette.websockets import WebSocketDisconnect

from terraforma.accounts.service import create_account
from terraforma.app import fight_timer
from terraforma.fights import live
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import inventory, service
from terraforma.testing import in_app_db

from .test_fight_store import SEED

ORIGIN = {"origin": "http://testserver"}
PASSWORD = "correct horse battery"
COMMAND = {
    "fighter": {"party": 0, "group": 0, "character": 0},
    "command": "attack_right",
    "target": {"party": 1, "group": 0, "character": 0},
}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


@pytest.fixture
def client(app_client):
    """Mike, logged out, with a hero on a team in a started fight against an ogre (``client.fight_id``)."""

    async def start(db):
        mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
        hero = await service.create_hero(db, mike, "Aria", "fighter")
        await inventory.add_item(db, hero, "sword", 1)
        await inventory.equip(db, hero, 0, 1)
        team = await service.create_team(db, mike, "Alpha")
        await service.add_to_team(db, mike, team.id, hero.id)
        return (await live.start_team_fight(db, team, ["ogre"], Rules())).id

    app_client.fight_id = in_app_db(app_client, start)
    return app_client


def log_in(client, username="Mike") -> str:
    answer = client.post("/api/login", json={"username": username, "password": PASSWORD})
    assert answer.status_code == 200, answer.text
    return answer.json()["csrf_token"]


def command(client, token, body=COMMAND, fight_id=None):
    return client.post(f"/api/fights/{fight_id or client.fight_id}/commands", json=body, headers={"X-CSRF-Token": token})


def stranger(client):
    """Zed, who has no hero in the fight, logged in (and Mike logged out)."""
    in_app_db(client, lambda db: create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True))
    client.post("/api/logout", headers={"X-CSRF-Token": "x"})
    return log_in(client, "Zed")


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True, "database": True}


def test_a_call_that_changes_something_needs_the_csrf_token(client):
    token = log_in(client)
    url = f"/api/fights/{client.fight_id}/commands"
    assert client.post(url, json=COMMAND).status_code == 403
    assert client.post(url, json=COMMAND, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert command(client, token).status_code == 202


def test_logging_in_again_replaces_the_token(client):
    first = log_in(client)
    second = log_in(client)
    assert first != second
    assert command(client, first).status_code == 403


@pytest.mark.parametrize(
    "change",
    [
        {"command": "cast_fireball_forever"},
        {"using": -1},
        {"using": "3"},
        {"fighter": {"party": 0, "group": 4, "character": 0}},
        {"target": {"party": 1, "group": 0}},
        {"extra": "field"},
    ],
    ids=["unknown-command", "negative", "string-number", "group-out-of-range", "missing-part", "extra-field"],
)
def test_a_call_of_the_wrong_shape_is_refused_before_any_game_code(client, change):
    token = log_in(client)
    assert command(client, token, {**COMMAND, **change}).status_code == 422


def test_calls_need_a_login(client):
    assert command(client, "x").status_code == 401


# --- commanding ------------------------------------------------------------------------------------------------------

def test_the_only_player_committing_plays_the_round_at_once(client):
    token = log_in(client)
    answer = command(client, token)
    assert answer.status_code == 202 and answer.json() == {"accepted": True, "round": 1, "played": True}
    view = client.get(f"/api/fights/{client.fight_id}").json()
    assert view["round"] == 2 and view["over"] is False


def test_a_fight_that_is_not_there_or_a_fighter_that_is_not_yours_is_refused(client):
    token = log_in(client)
    assert command(client, token, fight_id=999).status_code == 404
    assert command(client, token, {**COMMAND, "fighter": {"party": 1, "group": 0, "character": 0}}).status_code == 403
    assert command(client, token, {**COMMAND, "fighter": {"party": 0, "group": 3, "character": 9}}).status_code == 404
    assert command(client, token, {**COMMAND, "command": "item", "using": 99}).status_code == 409


def test_someone_with_no_hero_in_the_fight_cannot_command_in_it(client):
    token = stranger(client)
    assert command(client, token).status_code == 403


def test_a_fight_played_to_the_end_refuses_more_commands(client):
    token = log_in(client)
    for _ in range(40):
        if command(client, token).status_code != 202:
            break
    answer = command(client, token)
    assert answer.status_code == 409 and "over" in answer.json()["detail"]
    assert client.get(f"/api/fights/{client.fight_id}").json()["over"] is True


# --- looking -----------------------------------------------------------------------------------------------------------

def test_the_players_see_their_fight_and_others_see_it_only_by_its_public_name(client):
    log_in(client)
    mine = client.get(f"/api/fights/{client.fight_id}").json()
    assert [each["yours"] for each in mine["fighters"]] == [True, False] and len(mine["guid"]) == 32
    guid = mine["guid"]
    stranger(client)
    assert client.get(f"/api/fights/{client.fight_id}").status_code == 403, "by its number, only the players"
    watched = client.get(f"/api/fights/watch/{guid}").json()
    assert watched["id"] == client.fight_id and not any(each["yours"] for each in watched["fighters"])
    assert client.get("/api/fights/watch/" + "0" * 32).status_code == 404
    assert client.get("/api/fights/watch/not-a-name").status_code == 422
    assert client.get("/api/fights/999").status_code == 404


def test_looking_needs_a_login(client):
    assert client.get(f"/api/fights/{client.fight_id}").status_code == 401


# --- the socket -------------------------------------------------------------------------------------------------------------

def test_the_fight_socket_hears_who_committed_and_then_the_round(client):
    token = log_in(client)
    with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=ORIGIN) as socket:
        assert socket.receive_json() == {"type": "joined", "fight": client.fight_id, "watching": 1}
        assert command(client, token).status_code == 202
        committed = socket.receive_json()
        assert committed == {"type": "committed", "fight": client.fight_id, "round": 1, "fighter": COMMAND["fighter"]}, "what it was is not told"
        played = socket.receive_json()
        assert played["type"] == "round" and played["round"] == 1 and played["over"] is False and played["events"]
        assert played["events"][0][0] == "Turn" and played["deadline"].endswith("+00:00")


def test_the_fight_socket_hears_only_its_own_fight(client):
    token = log_in(client)
    with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=ORIGIN) as socket:
        socket.receive_json()
        command(client, token, fight_id=999)
        command(client, token)
        assert socket.receive_json()["fight"] == client.fight_id


def test_the_fight_socket_needs_a_login(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=ORIGIN) as socket:
            socket.receive_json()


def test_the_fight_socket_refuses_other_sites(client):
    log_in(client)
    for headers in ({"origin": "http://evil.example"}, {}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=headers) as socket:
                socket.receive_json()


def test_the_fight_socket_lets_in_only_the_players_or_those_with_its_public_name(client):
    log_in(client)
    guid = client.get(f"/api/fights/{client.fight_id}").json()["guid"]
    stranger(client)
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=ORIGIN) as socket:
            socket.receive_json()
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/fights/{client.fight_id}?guid={'0' * 32}", headers=ORIGIN) as socket:
            socket.receive_json()
    with client.websocket_connect(f"/ws/fights/{client.fight_id}?guid={guid}", headers=ORIGIN) as socket:
        assert socket.receive_json()["type"] == "joined"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/fights/999", headers=ORIGIN) as socket:
            socket.receive_json()


# --- the clock ---------------------------------------------------------------------------------------------------------------------

def test_the_timer_plays_a_round_nobody_committed_to_and_the_socket_hears_it(client, later):
    log_in(client)
    with client.websocket_connect(f"/ws/fights/{client.fight_id}", headers=ORIGIN) as socket:
        socket.receive_json()

        async def run_timer():
            task = asyncio.create_task(fight_timer(client.app, 0.01))
            await asyncio.sleep(0.2)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        client.portal.call(run_timer)
        assert client.get(f"/api/fights/{client.fight_id}").json()["round"] == 1, "its time has not run out"
        later(31)
        # The round takes as long as the database needs: wait for it, don't guess a time.
        timer = client.portal.start_task_soon(fight_timer, client.app, 0.01)
        try:
            deadline = time.monotonic() + 60
            while client.get(f"/api/fights/{client.fight_id}").json()["round"] < 2:
                assert time.monotonic() < deadline, "the timer never played the round"
                time.sleep(0.05)
            played = socket.receive_json()
        finally:
            timer.cancel()
        assert played["type"] == "round" and played["round"] == 1
    assert client.get(f"/api/fights/{client.fight_id}").json()["round"] == 2


def test_the_timer_is_on_by_default_and_the_tests_turn_it_off(client):
    from terraforma.settings import Settings

    assert Settings(session_secret="x" * 32).fight_timer_seconds == 1.0
    assert client.app.state.settings.fight_timer_seconds == 0
    assert in_app_db(client, lambda db: db.get(FightRecord, client.fight_id)) is not None


# --- finding fights ---------------------------------------------------------------------------------------------------------------

def test_the_list_shows_the_callers_fights_and_nobody_elses(client):
    assert client.get("/api/fights").status_code == 401
    log_in(client)
    [found] = client.get("/api/fights").json()
    assert found["id"] == client.fight_id and found["round"] == 1 and found["over"] is False and found["heroes"][0]["name"] == "Aria"
    assert client.get("/api/fights?running=true&limit=5").json() == [found]
    for bad in ("limit=0", "limit=51", "limit=x", "running=maybe"):
        assert client.get(f"/api/fights?{bad}").status_code == 422
    stranger(client)
    assert client.get("/api/fights").json() == []
