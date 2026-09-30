"""The web app: login, a typed call, and the fight WebSocket."""

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from terraforma.accounts.service import create_account
from terraforma.app import create_app
from terraforma.db.migrate import upgrade
from terraforma.db.session import make_engine, make_sessionmaker
from terraforma.settings import Settings

from .conftest import run

ORIGIN = {"origin": "http://testserver"}
COMMAND = {
    "fighter": {"party": 0, "group": 0, "character": 1},
    "command": "attack_left",
    "target": {"party": 1, "group": 0, "character": 0},
}


@pytest.fixture
def client(tmp_path):
    url = f"sqlite+aiosqlite:///{tmp_path}/app.db"

    async def setup():
        engine = make_engine(url)
        await upgrade(engine)
        async with make_sessionmaker(engine)() as session:
            await create_account(session, "Mike", "correct horse battery")
            await session.commit()
        await engine.dispose()

    run(setup())
    settings = Settings(database_url=url, session_secret="x" * 32, secure_cookies=False)
    with TestClient(create_app(settings)) as client:
        yield client


def log_in(client) -> str:
    answer = client.post("/api/login", json={"username": "mike", "password": "correct horse battery"})
    assert answer.status_code == 200, answer.text
    return answer.json()["csrf_token"]


def test_health(client):
    assert client.get("/api/health").json() == {"ok": True, "database": True}


def test_login(client):
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/login", json={"username": "Mike", "password": "wrong horse battery"}).status_code == 401
    log_in(client)
    assert client.get("/api/me").json() == {"username": "Mike"}


def test_a_call_that_changes_something_needs_the_csrf_token(client):
    token = log_in(client)
    assert client.post("/api/fights/1/commands", json=COMMAND).status_code == 403
    assert client.post("/api/fights/1/commands", json=COMMAND, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.post("/api/fights/1/commands", json=COMMAND, headers={"X-CSRF-Token": token}).status_code == 202


def test_logging_in_again_replaces_the_token(client):
    first = log_in(client)
    second = log_in(client)
    assert first != second
    assert client.post("/api/fights/1/commands", json=COMMAND, headers={"X-CSRF-Token": first}).status_code == 403


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
    answer = client.post("/api/fights/1/commands", json={**COMMAND, **change}, headers={"X-CSRF-Token": token})
    assert answer.status_code == 422


def test_calls_need_a_login(client):
    assert client.post("/api/fights/1/commands", json=COMMAND, headers={"X-CSRF-Token": "x"}).status_code == 401


def test_the_fight_socket_hears_commands_as_they_come(client):
    token = log_in(client)
    with client.websocket_connect("/ws/fights/7", headers=ORIGIN) as socket:
        assert socket.receive_json() == {"type": "joined", "fight": 7, "watching": 1}
        assert client.post("/api/fights/7/commands", json=COMMAND, headers={"X-CSRF-Token": token}).status_code == 202
        pushed = socket.receive_json()
        assert pushed["type"] == "command" and pushed["fight"] == 7 and pushed["command"] == "attack_left"
        assert pushed["target"] == COMMAND["target"]


def test_the_fight_socket_hears_only_its_own_fight(client):
    token = log_in(client)
    with client.websocket_connect("/ws/fights/7", headers=ORIGIN) as socket:
        socket.receive_json()
        client.post("/api/fights/8/commands", json=COMMAND, headers={"X-CSRF-Token": token})
        client.post("/api/fights/7/commands", json=COMMAND, headers={"X-CSRF-Token": token})
        assert socket.receive_json()["fight"] == 7, "fight 8's command never arrived here"


def test_the_fight_socket_needs_a_login(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/fights/7", headers=ORIGIN) as socket:
            socket.receive_json()


def test_the_fight_socket_refuses_other_sites(client):
    log_in(client)
    for headers in ({"origin": "http://evil.example"}, {}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/fights/7", headers=headers) as socket:
                socket.receive_json()


def test_about_names_the_game():
    from vanguard_tavern import GAME

    settings = Settings(database_url="sqlite+aiosqlite://", session_secret="x" * 32, secure_cookies=False)
    with TestClient(create_app(settings, GAME)) as client:
        assert client.get("/api/about").json()["game"] == "Vanguard Tavern"
