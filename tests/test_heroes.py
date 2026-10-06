"""Heroes and teams: made, renamed and removed by their own account only, with limits, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.heroes.models import Hero, TeamMember
from terraforma.testing import in_app_db

PASSWORD = "correct horse battery"

SEED = {
    "abilities": [{"key": "slash", "name": "Slash", "kind": "skill"}],
    "jobs": [
        {"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10.4, "Strength": 3.6}, "abilities": ["slash"]},
        {"key": "mage", "name": "Mage", "stat_growth": {"MP": 8.0}},
    ],
}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


@pytest.fixture
def mike(app_client):
    return app_client, sign_in(app_client, "Mike")


def new_hero(client, headers, name="Aria", job="fighter"):
    return client.post("/api/heroes", json={"name": name, "job": job}, headers=headers)


# --- heroes -------------------------------------------------------------------------------

def test_the_jobs_a_hero_can_take_are_listed_with_their_starting_stats_to_a_logged_in_player(mike, app_client):
    client, _ = mike
    jobs = client.get("/api/jobs").json()
    assert [(job["key"], job["name"]) for job in jobs] == [("fighter", "Fighter"), ("mage", "Mage")]
    assert {name: value for name, value in jobs[0]["stats"].items() if value} == {"HP": 10, "Strength": 4}, "one level of growth, rounded"
    assert {name: value for name, value in jobs[1]["stats"].items() if value} == {"MP": 8}
    client.cookies.clear()
    assert client.get("/api/jobs").status_code == 401


def test_a_new_hero_starts_at_level_one_with_the_jobs_stats_on_the_hub(mike):
    client, headers = mike
    answer = new_hero(client, headers)
    assert answer.status_code == 201, answer.text
    hero = answer.json()
    assert (hero["name"], hero["job"], hero["level"], hero["xp"]) == ("Aria", "fighter", 1, 0)
    assert (hero["stats"]["HP"], hero["stats"]["Strength"], hero["stats"]["MP"]) == (10, 4, 0), "the job's growth, rounded"
    assert hero["place"] == {"map": "hub", "x": 0, "y": 0}
    assert client.get("/api/heroes").json() == [hero]


def test_heroes_need_a_real_active_job_and_a_good_name(mike):
    client, headers = mike
    assert new_hero(client, headers, job="wizard").status_code == 404
    for name in ("A", "x" * 25, "Ar  ia", "-Aria", "Ari<a>"):
        assert new_hero(client, headers, name=name).status_code == 422, name
    assert new_hero(client, headers, name="O'Brien the 2nd").status_code == 201


def test_hero_names_are_unique_per_account_ignoring_case_and_spacing(mike, app_client):
    client, headers = mike
    new_hero(client, headers)
    assert new_hero(client, headers, name="ARIA").status_code == 409
    other = sign_in(client, "Ann")
    assert new_hero(client, other).status_code == 201, "another account may use the name"


def test_an_account_has_a_limit_of_heroes(mike, monkeypatch):
    client, headers = mike
    monkeypatch.setattr(service, "MAX_HEROES", 2)
    assert [new_hero(client, headers, name=f"Hero {n}").status_code for n in range(3)] == [201, 201, 422]


def test_renaming_and_deleting_a_hero(mike):
    client, headers = mike
    hero = new_hero(client, headers).json()
    new_hero(client, headers, name="Bob")
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "Aria Two"}, headers=headers).json()["name"] == "Aria Two"
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "bob"}, headers=headers).status_code == 409
    assert client.post(f"/api/heroes/{hero['id']}/delete", headers=headers).status_code == 200
    assert [entry["name"] for entry in client.get("/api/heroes").json()] == ["Bob"]
    assert client.post(f"/api/heroes/{hero['id']}/delete", headers=headers).status_code == 404


def test_nobody_touches_another_accounts_heroes(mike):
    client, headers = mike
    hero = new_hero(client, headers).json()
    other = sign_in(client, "Ann")
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "Mine"}, headers=other).status_code == 404
    assert client.post(f"/api/heroes/{hero['id']}/delete", headers=other).status_code == 404
    assert client.get("/api/heroes").json() == [], "Ann sees none of Mike's"


def test_the_hero_calls_need_login_and_the_csrf_token(app_client):
    assert app_client.get("/api/heroes").status_code == 401
    assert app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}).status_code == 401
    headers = sign_in(app_client, "Mike")
    assert app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}).status_code == 403
    assert app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter", "extra": 1}, headers=headers).status_code == 422


def test_a_retired_job_cannot_be_chosen(mike, game):
    client, headers = mike
    (game.seed_dir / "jobs.json").write_text(json.dumps([SEED["jobs"][1]]))
    from terraforma.content.loader import load_content
    from terraforma.seed import load_seed

    in_app_db(client, lambda db: load_content(db, load_seed(game.seed_dir)))
    assert new_hero(client, headers, job="fighter").status_code == 404
    assert new_hero(client, headers, job="mage").status_code == 201


# --- teams --------------------------------------------------------------------------------------

def test_a_team_holds_heroes_in_slots(mike):
    client, headers = mike
    heroes = [new_hero(client, headers, name=f"Hero {n}").json()["id"] for n in range(3)]
    team = client.post("/api/teams", json={"name": "Alpha"}, headers=headers).json()
    assert team == {"id": team["id"], "name": "Alpha", "members": []}
    for hero_id in heroes[:2]:
        assert client.post(f"/api/teams/{team['id']}/add-hero", json={"hero_id": hero_id}, headers=headers).status_code == 200
    assert [member["slot"] for member in client.get("/api/teams").json()[0]["members"]] == [0, 1]
    client.post(f"/api/teams/{team['id']}/remove-hero", json={"hero_id": heroes[0]}, headers=headers)
    added = client.post(f"/api/teams/{team['id']}/add-hero", json={"hero_id": heroes[2]}, headers=headers).json()
    assert added["slot"] == 0, "the freed slot is used first"
    assert client.post(f"/api/teams/{team['id']}/remove-hero", json={"hero_id": heroes[0]}, headers=headers).status_code == 404


def test_a_hero_is_on_one_team_and_a_team_has_limited_room(mike, monkeypatch):
    client, headers = mike
    monkeypatch.setattr(service, "TEAM_SIZE", 1)
    first, second = (new_hero(client, headers, name=name).json()["id"] for name in ("Aria", "Bob"))
    alpha = client.post("/api/teams", json={"name": "Alpha"}, headers=headers).json()["id"]
    beta = client.post("/api/teams", json={"name": "Beta"}, headers=headers).json()["id"]
    assert client.post(f"/api/teams/{alpha}/add-hero", json={"hero_id": first}, headers=headers).status_code == 200
    assert client.post(f"/api/teams/{beta}/add-hero", json={"hero_id": first}, headers=headers).status_code == 422
    assert client.post(f"/api/teams/{alpha}/add-hero", json={"hero_id": second}, headers=headers).status_code == 422, "full"


def test_team_names_limits_and_ownership(mike, monkeypatch):
    client, headers = mike
    assert client.post("/api/teams", json={"name": "Alpha"}, headers=headers).status_code == 201
    assert client.post("/api/teams", json={"name": "alpha"}, headers=headers).status_code == 409
    monkeypatch.setattr(service, "MAX_TEAMS", 1)
    assert client.post("/api/teams", json={"name": "Beta"}, headers=headers).status_code == 422
    team = client.get("/api/teams").json()[0]["id"]
    hero = new_hero(client, headers).json()["id"]
    other = sign_in(client, "Ann")
    assert client.post(f"/api/teams/{team}/rename", json={"name": "Mine"}, headers=other).status_code == 404
    assert client.post(f"/api/teams/{team}/add-hero", json={"hero_id": hero}, headers=other).status_code == 404
    assert client.post(f"/api/teams/{team}/delete", headers=other).status_code == 404
    mine = client.post("/api/teams", json={"name": "Anns"}, headers=other).json()["id"]
    assert client.post(f"/api/teams/{mine}/add-hero", json={"hero_id": hero}, headers=other).status_code == 404, "not her hero"


def test_deleting_a_hero_or_a_team_cleans_up_membership(mike):
    client, headers = mike
    hero = new_hero(client, headers).json()["id"]
    team = client.post("/api/teams", json={"name": "Alpha"}, headers=headers).json()["id"]
    client.post(f"/api/teams/{team}/add-hero", json={"hero_id": hero}, headers=headers)
    assert client.post(f"/api/heroes/{hero}/delete", headers=headers).status_code == 200
    assert client.get("/api/teams").json()[0]["members"] == []
    other = new_hero(client, headers, name="Bob").json()["id"]
    client.post(f"/api/teams/{team}/add-hero", json={"hero_id": other}, headers=headers)
    assert client.post(f"/api/teams/{team}/delete", headers=headers).status_code == 200

    async def counts(db):
        return await db.scalar(select(func.count()).select_from(TeamMember)), await db.scalar(select(func.count()).select_from(Hero))

    assert in_app_db(client, counts) == (0, 1), "the hero stays; only the membership went"
