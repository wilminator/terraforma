"""Where a new team starts: the game's ``Start`` offers places (the capitals), the player picks one when they make a team, and heroes added
later stand where their team does. Over HTTP, on every database."""

import json

import pytest

from terraforma.accounts.service import create_account
from terraforma.game import Game
from terraforma.heroes.hooks import Start, StartPlace
from terraforma.models import Map
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}}]}
PLACES = [StartPlace("aldergate", "Aldergate", "aldergate", 5, 6), StartPlace("solhaven", "Solhaven", "solhaven", 2, 3)]


class Capitals(Start):
    async def places(self, session, account):
        return PLACES


def a_game(tmp_path, start):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, start=start)


@pytest.fixture(params=["capitals"])
def game(request, tmp_path):
    return a_game(tmp_path, Capitals() if request.param == "capitals" else Start())


@pytest.fixture
def mike(app_client, game):
    async def setup(db):
        hub = await ensure_start(db)
        for name in ("aldergate", "solhaven"):
            db.add(Map(world_id=hub.world_id, name=name))
        await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)

    in_app_db(app_client, setup)
    token = app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    return app_client, {"X-CSRF-Token": token}


def where(client, name):
    return next(entry for entry in client.get("/api/heroes").json() if entry["name"] == name)["place"]


def team(client, headers, name, hero, **extra):
    return client.post("/api/teams", json={"name": name, "heroes": [{"name": hero, "job": "fighter"}], **extra}, headers=headers)


none = pytest.mark.parametrize("game", ["none"], indirect=True)


def test_the_places_offered_are_listed(mike):
    client, _ = mike
    assert client.get("/api/start-places").json() == [{"key": "aldergate", "title": "Aldergate"}, {"key": "solhaven", "title": "Solhaven"}]


def test_the_player_picks_where_a_new_team_starts(mike):
    client, headers = mike
    assert team(client, headers, "Alpha", "Aria", start="solhaven").status_code == 201
    assert where(client, "Aria") == {"map": "solhaven", "x": 2, "y": 3}


def test_without_a_pick_the_first_place_is_used(mike):
    client, headers = mike
    assert team(client, headers, "Alpha", "Aria").status_code == 201
    assert where(client, "Aria") == {"map": "aldergate", "x": 5, "y": 6}


def test_a_place_the_game_does_not_offer_is_refused_and_nothing_is_made(mike):
    client, headers = mike
    assert team(client, headers, "Alpha", "Aria", start="gallows").status_code == 422
    assert client.get("/api/teams").json() == []


def test_a_place_missing_from_the_world_is_refused(app_client, game):
    in_app_db(app_client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    assert team(app_client, headers, "Alpha", "Aria", start="solhaven").status_code == 422


@none
def test_a_game_with_no_places_starts_heroes_on_the_hub(mike):
    client, headers = mike
    assert client.get("/api/start-places").json() == []
    assert team(client, headers, "Alpha", "Aria").status_code == 201
    assert where(client, "Aria") == {"map": "hub", "x": 0, "y": 0}


@none
def test_a_game_with_no_places_refuses_a_pick(mike):
    client, headers = mike
    assert team(client, headers, "Alpha", "Aria", start="solhaven").status_code == 422


def test_a_hero_added_to_a_team_stands_where_the_team_does(mike):
    client, headers = mike
    made = team(client, headers, "Alpha", "Aria", start="solhaven").json()
    added = client.post(f"/api/teams/{made['id']}/heroes", json={"name": "Bram", "job": "fighter"}, headers=headers)
    assert added.status_code == 201 and where(client, "Bram") == {"map": "solhaven", "x": 2, "y": 3}
    refused = client.post(f"/api/teams/{made['id']}/heroes", json={"name": "Cleo", "job": "fighter", "start": "aldergate"}, headers=headers)
    assert refused.status_code == 422, "the team has heroes already"


def test_the_first_hero_of_an_empty_team_starts_where_the_player_picks(mike):
    client, headers = mike
    empty = client.post("/api/teams", json={"name": "Alpha", "start": "aldergate"}, headers=headers).json()
    client.post(f"/api/teams/{empty['id']}/heroes", json={"name": "Aria", "job": "fighter", "start": "solhaven"}, headers=headers)
    assert where(client, "Aria") == {"map": "solhaven", "x": 2, "y": 3}


def test_a_replacement_hero_stands_where_the_one_it_replaces_did(mike, monkeypatch):
    client, headers = mike
    from terraforma.heroes.hooks import Roster

    async def yes(self, *args):
        return None

    monkeypatch.setattr(Roster, "may_remove", yes)
    made = team(client, headers, "Alpha", "Aria", start="solhaven").json()
    old = where(client, "Aria")
    hero_id = made["members"][0]["hero_id"]
    assert client.post(f"/api/teams/{made['id']}/heroes/{hero_id}/replace", json={"name": "Bram", "job": "fighter"}, headers=headers).status_code == 201
    assert where(client, "Bram") == old
