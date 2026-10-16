"""Play time: wall-clock seconds credited by the calls a player's input drives and by fight playback. On every database."""

import json

import pytest

from terraforma.accounts.service import create_account
from terraforma.fights import live
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.playtime import service as playtime
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import PASSWORD, SEED

pytestmark = pytest.mark.social

RULES = Rules()


# --- the counting (service) ------------------------------------------------------------------------------------------

@pytest.fixture
async def mike(db):
    return await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)


async def played(db, mike):
    return await playtime.seconds_played(db, RULES, mike.id)


@pytest.mark.anyio
async def test_an_account_that_never_acted_has_played_nothing(db, mike):
    assert await played(db, mike) == 0


@pytest.mark.anyio
async def test_an_input_credits_the_next_ten_seconds(db, mike):
    await playtime.credit_input(db, RULES, mike.id)
    assert await played(db, mike) == 10


@pytest.mark.anyio
async def test_the_next_input_coming_first_cuts_the_credit_short(db, mike, later):
    await playtime.credit_input(db, RULES, mike.id)
    later(4)
    await playtime.credit_input(db, RULES, mike.id)
    assert await played(db, mike) == 14, "4 seconds for the first input, then the next ten"
    later(34)
    await playtime.credit_input(db, RULES, mike.id)
    assert await played(db, mike) == 24, "a pause counts for nothing beyond the ten"


@pytest.mark.anyio
async def test_inputs_at_the_same_second_count_once(db, mike):
    for _ in range(5):
        await playtime.credit_input(db, RULES, mike.id)
    assert await played(db, mike) == 10


@pytest.mark.anyio
async def test_a_game_sets_how_much_an_input_credits(db, mike, later):
    class Brief(Rules):
        play_credit_seconds = 3

    await playtime.credit_input(db, Brief(), mike.id)
    later(20)
    await playtime.credit_input(db, Brief(), mike.id)
    assert await playtime.seconds_played(db, Brief(), mike.id) == 6


@pytest.mark.anyio
async def test_playback_counts_from_the_round_to_now_and_not_twice_with_the_inputs(db, mike, later):
    from tests.conftest import START

    await playtime.credit_input(db, RULES, mike.id)  # at 0
    later(40)
    await playtime.credit_input(db, RULES, mike.id)
    await playtime.credit_playback(db, RULES, mike.id, START)  # the round played at 0, shown by 40
    assert await played(db, mike) == 50, "40 seconds from the round on, then the next input's ten"
    await playtime.credit_playback(db, RULES, mike.id, START)
    assert await played(db, mike) == 50, "saying it again earns nothing"


@pytest.mark.anyio
async def test_playback_counts_at_most_the_cap(db, mike, later):
    from tests.conftest import START

    await playtime.credit_input(db, RULES, mike.id)  # the player's command that played the round, at 0
    later(3000)
    await playtime.credit_input(db, RULES, mike.id)
    await playtime.credit_playback(db, RULES, mike.id, START)
    assert await played(db, mike) == 300 + 10


@pytest.mark.anyio
async def test_a_game_sets_the_playback_cap(db, mike, later):
    from tests.conftest import START

    class Short(Rules):
        playback_cap_seconds = 60

    later(3000)
    await playtime.credit_playback(db, Short(), mike.id, START)
    assert await playtime.seconds_played(db, Short(), mike.id) == 60


@pytest.mark.anyio
async def test_play_time_never_runs_ahead_of_the_clock(db, mike, later):
    from tests.conftest import START

    await playtime.credit_input(db, RULES, mike.id)
    for moment in (0, 5, 5, 8, 20):
        later(moment)
        await playtime.credit_input(db, RULES, mike.id)
        await playtime.credit_playback(db, RULES, mike.id, START)
    assert await played(db, mike) == 30, "twenty seconds since the first input, and the last input's ten"


# --- the calls ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


@pytest.fixture
def client(app_client):
    """Mike, logged out, with a hero in a started fight against an ogre (``client.fight_id``)."""

    async def start(db):
        mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
        hero = await service.create_hero(db, mike, "Aria", "fighter")
        team = await service.create_team(db, mike, "Alpha")
        await service.add_to_team(db, mike, team.id, hero.id)
        return (await live.start_team_fight(db, team, ["ogre"], Rules())).id

    app_client.fight_id = in_app_db(app_client, start)
    return app_client


def log_in(client, username="Mike"):
    return {"X-CSRF-Token": expect(client.post("/api/login", json={"username": username, "password": PASSWORD}), 200).json()["csrf_token"]}


COMMAND = {"fighter": {"party": 0, "group": 0, "character": 0}, "command": "attack_right", "target": {"party": 1, "group": 0, "character": 0}}


def play(client, headers):
    """Mike commands, which plays round 1 (his is the only hero)."""
    answer = expect(client.post(f"/api/fights/{client.fight_id}/commands", json=COMMAND, headers=headers), 202).json()
    assert answer["played"] is True
    return answer


def done(client, headers, round_=1, fight_id=None):
    return client.post(f"/api/fights/{fight_id or client.fight_id}/playback-done", json={"round": round_}, headers=headers)


def test_play_time_needs_a_login(client):
    assert client.get("/api/play-time").status_code == 401


def test_a_call_that_changes_something_credits_ten_seconds_and_a_read_credits_none(client, later):
    headers = log_in(client)
    assert expect(client.get("/api/play-time"), 200).json() == {"seconds": 0}
    expect(client.get(f"/api/fights/{client.fight_id}"), 200)
    assert client.get("/api/play-time").json() == {"seconds": 0}, "logging in and reading do not count"
    play(client, headers)
    assert client.get("/api/play-time").json() == {"seconds": 10}
    later(3)
    expect(client.post("/api/settings/round-time", json={"multiplier": 1.5}, headers=headers), 200)
    assert client.get("/api/play-time").json() == {"seconds": 13}


def test_a_call_refused_for_its_csrf_token_credits_nothing(client):
    log_in(client)
    assert client.post(f"/api/fights/{client.fight_id}/commands", json=COMMAND, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert client.get("/api/play-time").json() == {"seconds": 0}


def test_playback_done_counts_the_time_since_the_round_played(client, later):
    headers = log_in(client)
    play(client, headers)  # round 1 plays now
    later(40)
    assert expect(done(client, headers), 200).json() == {"seconds": 50}
    assert expect(done(client, headers), 200).json() == {"seconds": 50}, "the same stretch is not counted twice"
    assert client.get("/api/play-time").json() == {"seconds": 50}


def test_playback_is_capped_so_a_closed_tab_does_not_earn(client, later):
    headers = log_in(client)
    play(client, headers)
    later(3600)
    assert expect(done(client, headers), 200).json() == {"seconds": 300 + 10}


def test_playback_done_for_a_round_not_played_or_a_fight_not_yours_or_a_bad_body_is_refused(client):
    headers = log_in(client)
    play(client, headers)
    assert done(client, headers, 2).status_code == 404, "round 2 has not played"
    assert done(client, headers, fight_id=client.fight_id + 99).status_code == 404
    assert client.post(f"/api/fights/{client.fight_id}/playback-done", json={"round": 0}, headers=headers).status_code == 422
    assert client.post(f"/api/fights/{client.fight_id}/playback-done", json={"round": "1"}, headers=headers).status_code == 422
    assert client.post(f"/api/fights/{client.fight_id}/playback-done", json={"round": 1, "extra": 1}, headers=headers).status_code == 422
    assert done(client, {}).status_code == 403, "it needs the CSRF token"
    in_app_db(client, lambda db: create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True))
    client.post("/api/logout", headers=headers)
    zed = log_in(client, "Zed")
    assert done(client, zed).status_code == 403, "Zed has no hero in that fight"
    assert client.get("/api/play-time").json() == {"seconds": 10}, "only his own input counted: 10 seconds"
