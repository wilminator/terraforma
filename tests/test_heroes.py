"""Heroes and teams: a team is made with its heroes, which never exist without one; changing a saved team is the game's to allow."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.fights.rules import Rules
from terraforma.game import Game
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


def log_in(client, username):
    """Logs in again as an account that exists (the client holds one login at a time): its CSRF headers."""
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


@pytest.fixture
def mike(app_client):
    return app_client, sign_in(app_client, "Mike")


def hero_spec(name, job="fighter"):
    return {"name": name, "job": job}


def save_team(client, headers, name="Alpha", heroes=("Aria",), job="fighter"):
    """A team made with its heroes, the one way the browser makes them."""
    return client.post("/api/teams", json={"name": name, "heroes": [hero_spec(each, job) for each in heroes]}, headers=headers)


def hero_named(client, name):
    return next(entry for entry in client.get("/api/heroes").json() if entry["name"] == name)


def members_of(client, team_id):
    team = next(entry for entry in client.get("/api/teams").json() if entry["id"] == team_id)
    return [(member["slot"], member["name"]) for member in team["members"]]


def allow(monkeypatch, *rules):
    """The game allows what its roster refuses by default (a game that sells it, or plays it free, says so in the rule)."""
    from terraforma.heroes.hooks import Roster

    async def yes(self, *args):
        return None

    for rule in rules:
        monkeypatch.setattr(Roster, rule, yes)


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
    assert save_team(client, headers).status_code == 201
    hero = hero_named(client, "Aria")
    assert (hero["name"], hero["job"], hero["level"], hero["xp"]) == ("Aria", "fighter", 1, 0)
    assert (hero["stats"]["HP"], hero["stats"]["Strength"], hero["stats"]["MP"]) == (10, 4, 0), "the job's growth, rounded"
    assert hero["place"] == {"map": "hub", "x": 0, "y": 0}
    assert client.get("/api/heroes").json() == [hero]


def test_heroes_need_a_real_active_job_and_a_good_name(mike):
    client, headers = mike
    assert save_team(client, headers, heroes=("Aria",), job="wizard").status_code == 404
    for name in ("A", "x" * 25, "Ar  ia", "-Aria", "Ari<a>"):
        assert save_team(client, headers, heroes=(name,)).status_code == 422, name
    assert client.get("/api/teams").json() == [], "a team that is refused leaves nothing behind"
    assert client.get("/api/heroes").json() == []
    assert save_team(client, headers, heroes=("O'Brien the 2nd",)).status_code == 201


def test_hero_names_are_unique_per_account_ignoring_case_and_spacing(mike, app_client):
    client, headers = mike
    save_team(client, headers, heroes=("Aria",))
    assert save_team(client, headers, name="Beta", heroes=("ARIA",)).status_code == 409
    assert save_team(client, headers, name="Gamma", heroes=("Bob", "bob")).status_code == 409, "twice in one team"
    assert [team["name"] for team in client.get("/api/teams").json()] == ["Alpha"]
    other = sign_in(client, "Ann")
    assert save_team(client, other).status_code == 201, "another account may use the name"


def test_renaming_a_hero(mike):
    client, headers = mike
    save_team(client, headers, heroes=("Aria", "Bob"))
    hero = hero_named(client, "Aria")
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "Aria Two"}, headers=headers).json()["name"] == "Aria Two"
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "bob"}, headers=headers).status_code == 409


def test_nobody_touches_another_accounts_heroes(mike):
    client, headers = mike
    team = save_team(client, headers).json()["id"]
    hero = hero_named(client, "Aria")
    other = sign_in(client, "Ann")
    assert client.post(f"/api/heroes/{hero['id']}/rename", json={"name": "Mine"}, headers=other).status_code == 404
    assert client.post(f"/api/teams/{team}/heroes/{hero['id']}/delete", headers=other).status_code == 404
    assert client.get("/api/heroes").json() == [], "Ann sees none of Mike's"


def test_the_hero_and_team_calls_need_login_and_the_csrf_token(app_client):
    body = {"name": "Alpha", "heroes": [hero_spec("Aria")]}
    assert app_client.get("/api/heroes").status_code == 401
    assert app_client.get("/api/team-rules").status_code == 401
    assert app_client.post("/api/teams", json=body).status_code == 401
    headers = sign_in(app_client, "Mike")
    assert app_client.post("/api/teams", json=body).status_code == 403
    assert app_client.post("/api/teams", json={**body, "extra": 1}, headers=headers).status_code == 422
    assert app_client.post("/api/heroes", json=hero_spec("Aria"), headers=headers).status_code in (404, 405), "there is no hero without a team"


def test_a_retired_job_cannot_be_chosen(mike, game):
    client, headers = mike
    (game.seed_dir / "jobs.json").write_text(json.dumps([SEED["jobs"][1]]))
    from terraforma.content.loader import load_content
    from terraforma.seed import load_seed

    in_app_db(client, lambda db: load_content(db, load_seed(game.seed_dir)))
    assert save_team(client, headers, heroes=("Aria",), job="fighter").status_code == 404
    assert save_team(client, headers, heroes=("Aria",), job="mage").status_code == 201


# --- teams --------------------------------------------------------------------------------------

def test_a_team_is_saved_with_its_heroes_in_slots(mike):
    client, headers = mike
    answer = save_team(client, headers, heroes=("Aria", "Bob", "Cato"))
    assert answer.status_code == 201, answer.text
    team = answer.json()
    assert (team["name"], [(member["slot"], member["name"]) for member in team["members"]]) == ("Alpha", [(0, "Aria"), (1, "Bob"), (2, "Cato")])
    assert client.get("/api/teams").json() == [team]


def test_a_team_is_made_first_and_filled_in_after_and_holds_the_games_maximum_of_heroes(mike, monkeypatch):
    client, headers = mike
    assert client.get("/api/team-rules").json() == {"team_min": 1, "team_max": 4, "max_teams": 8}
    empty = save_team(client, headers, heroes=())
    assert empty.status_code == 201 and empty.json()["members"] == [], "a team can be made with nobody on it"
    assert client.post("/api/teams", json={"name": "Beta"}, headers=headers).status_code == 201, "the heroes may be left out of the call"
    assert save_team(client, headers, name="Gamma", heroes=("A1", "A2", "A3", "A4", "A5")).status_code == 422, "above the maximum"
    assert len(client.get("/api/teams").json()) == 2 and client.get("/api/heroes").json() == []
    monkeypatch.setattr(Rules, "team_min", 2)
    monkeypatch.setattr(Rules, "team_max", 3)
    assert client.get("/api/team-rules").json() == {"team_min": 2, "team_max": 3, "max_teams": 8}
    assert save_team(client, headers, name="Delta", heroes=("A1", "A2", "A3", "A4")).status_code == 422
    assert save_team(client, headers, name="Delta", heroes=("A1", "A2")).status_code == 201
    monkeypatch.setattr(Rules, "team_max", 9)
    assert client.get("/api/team-rules").json()["team_max"] == 5, "the engine's screens draw at most five"
    assert save_team(client, headers, name="Eps", heroes=("B1", "B2", "B3", "B4", "B5")).status_code == 201
    assert save_team(client, headers, name="Zeta", heroes=("C1", "C2", "C3", "C4", "C5", "C6")).status_code == 422


def test_a_team_with_too_few_heroes_cannot_play_until_it_has_the_minimum(mike, monkeypatch):
    client, headers = mike
    monkeypatch.setattr(Rules, "team_min", 2)
    team = save_team(client, headers, heroes=("Aria",)).json()["id"]
    refused = client.post(f"/api/teams/{team}/play", headers=headers)
    assert refused.status_code == 409 and "at least 2 heroes" in refused.json()["detail"]
    assert client.post(f"/api/teams/{team}/heroes", json=hero_spec("Bob"), headers=headers).status_code == 201
    assert client.post(f"/api/teams/{team}/play", headers=headers).status_code == 200


def test_a_player_has_the_number_of_teams_the_game_allows(mike, monkeypatch):
    client, headers = mike
    monkeypatch.setattr(Rules, "max_teams", 2)
    assert [save_team(client, headers, name=name, heroes=(f"{name}1",)).status_code for name in ("Alpha", "Beta", "Gamma")] == [201, 201, 422]
    assert save_team(client, headers, name="alpha", heroes=("Other",)).status_code in (409, 422)


def test_a_hero_is_added_while_the_team_has_room(mike, monkeypatch):
    client, headers = mike
    monkeypatch.setattr(Rules, "team_max", 2)
    team = save_team(client, headers).json()["id"]
    added = client.post(f"/api/teams/{team}/heroes", json=hero_spec("Bob", "mage"), headers=headers)
    assert added.status_code == 201 and added.json()["job"] == "mage"
    assert members_of(client, team) == [(0, "Aria"), (1, "Bob")]
    assert client.post(f"/api/teams/{team}/heroes", json=hero_spec("Cato"), headers=headers).status_code == 422, "full"
    assert client.post(f"/api/teams/{team}/heroes", json=hero_spec("Cato"), headers=sign_in(client, "Ann")).status_code == 404


def test_removing_and_replacing_a_hero_are_refused_unless_the_game_allows_them(mike, monkeypatch):
    client, headers = mike
    team = save_team(client, headers, heroes=("Aria", "Bob")).json()["id"]
    aria = hero_named(client, "Aria")["id"]
    refused = client.post(f"/api/teams/{team}/heroes/{aria}/delete", headers=headers)
    assert refused.status_code == 422 and "removed or replaced" in refused.json()["detail"]
    assert client.post(f"/api/teams/{team}/heroes/{aria}/replace", json=hero_spec("Dax"), headers=headers).status_code == 422
    assert members_of(client, team) == [(0, "Aria"), (1, "Bob")]

    allow(monkeypatch, "may_remove")
    replaced = client.post(f"/api/teams/{team}/heroes/{aria}/replace", json=hero_spec("Dax", "mage"), headers=headers)
    assert replaced.status_code == 201 and replaced.json()["job"] == "mage"
    assert members_of(client, team) == [(0, "Dax"), (1, "Bob")], "the new hero has the old one's place"
    assert [entry["name"] for entry in client.get("/api/heroes").json()] == ["Bob", "Dax"], "the old hero is gone"
    assert client.post(f"/api/teams/{team}/heroes/{aria}/delete", headers=headers).status_code == 404

    bob = hero_named(client, "Bob")["id"]
    assert client.post(f"/api/teams/{team}/heroes/{bob}/delete", headers=headers).status_code == 200
    assert members_of(client, team) == [(0, "Dax")]
    only = hero_named(client, "Dax")["id"]
    assert client.post(f"/api/teams/{team}/heroes/{only}/delete", headers=headers).status_code == 200
    assert members_of(client, team) == [], "the places stay empty for new heroes"
    filled = client.post(f"/api/teams/{team}/heroes", json=hero_spec("Eve"), headers=headers)
    assert filled.status_code == 201 and members_of(client, team) == [(0, "Eve")]


def test_a_team_below_the_games_minimum_cannot_play_until_its_places_are_filled(mike, monkeypatch):
    client, headers = mike
    allow(monkeypatch, "may_remove")
    monkeypatch.setattr(Rules, "team_min", 2)
    team = save_team(client, headers, heroes=("Aria", "Bob")).json()["id"]
    assert client.post(f"/api/teams/{team}/heroes/{hero_named(client, 'Bob')['id']}/delete", headers=headers).status_code == 200
    refused = client.post(f"/api/teams/{team}/play", headers=headers)
    assert refused.status_code == 409 and "at least 2 heroes" in refused.json()["detail"]
    assert client.post(f"/api/teams/{team}/heroes", json=hero_spec("Cato"), headers=headers).status_code == 201
    assert client.post(f"/api/teams/{team}/play", headers=headers).status_code == 200


def test_a_team_that_has_entered_the_game_says_which_party_it_is_in(mike):
    client, headers = mike
    team = save_team(client, headers).json()["id"]
    assert client.get("/api/teams").json()[0]["party"] is None
    party = client.post(f"/api/teams/{team}/play", headers=headers).json()["party"]
    assert client.get("/api/teams").json()[0]["party"] == party


def test_a_replacement_is_checked_before_the_hero_goes(mike, monkeypatch):
    client, headers = mike
    allow(monkeypatch, "may_remove")
    team = save_team(client, headers, heroes=("Aria", "Bob")).json()["id"]
    aria = hero_named(client, "Aria")["id"]
    assert client.post(f"/api/teams/{team}/heroes/{aria}/replace", json=hero_spec("Dax", "wizard"), headers=headers).status_code == 404
    assert client.post(f"/api/teams/{team}/heroes/{aria}/replace", json=hero_spec("-"), headers=headers).status_code == 422
    assert client.post(f"/api/teams/{team}/heroes/{aria}/replace", json=hero_spec("bob"), headers=headers).status_code == 409
    assert members_of(client, team) == [(0, "Aria"), (1, "Bob")], "nothing was lost"


def test_moving_and_exchanging_heroes_between_teams_are_refused_unless_the_game_allows_them(mike, monkeypatch):
    client, headers = mike
    alpha = save_team(client, headers, name="Alpha", heroes=("Aria", "Bob")).json()["id"]
    beta = save_team(client, headers, name="Beta", heroes=("Cato", "Dax")).json()["id"]
    aria, cato = hero_named(client, "Aria")["id"], hero_named(client, "Cato")["id"]
    assert client.post(f"/api/heroes/{aria}/move", json={"team_id": beta}, headers=headers).status_code == 422
    assert client.post("/api/heroes/swap", json={"hero_id": aria, "with_hero_id": cato}, headers=headers).status_code == 422

    allow(monkeypatch, "may_move")
    assert client.post("/api/heroes/swap", json={"hero_id": aria, "with_hero_id": cato}, headers=headers).status_code == 200
    assert members_of(client, alpha) == [(0, "Cato"), (1, "Bob")] and members_of(client, beta) == [(0, "Aria"), (1, "Dax")]
    assert client.post("/api/heroes/swap", json={"hero_id": aria, "with_hero_id": aria}, headers=headers).status_code == 422
    bob = hero_named(client, "Bob")["id"]
    assert client.post("/api/heroes/swap", json={"hero_id": cato, "with_hero_id": bob}, headers=headers).status_code == 422, "the same team"

    moved = client.post(f"/api/heroes/{bob}/move", json={"team_id": beta}, headers=headers)
    assert moved.status_code == 200 and moved.json()["slot"] == 2
    assert members_of(client, alpha) == [(0, "Cato")] and members_of(client, beta) == [(0, "Aria"), (1, "Dax"), (2, "Bob")]
    assert client.post(f"/api/heroes/{cato}/move", json={"team_id": beta}, headers=headers).status_code == 200
    assert members_of(client, alpha) == [], "the team it left has an empty place"
    assert client.post(f"/api/heroes/{bob}/move", json={"team_id": beta}, headers=headers).status_code == 422, "already there"


def test_a_move_needs_room_and_your_own_teams(mike, monkeypatch):
    client, headers = mike
    allow(monkeypatch, "may_move")
    monkeypatch.setattr(Rules, "team_max", 2)
    alpha = save_team(client, headers, name="Alpha", heroes=("Aria", "Bob")).json()["id"]
    beta = save_team(client, headers, name="Beta", heroes=("Cato", "Dax")).json()["id"]
    aria = hero_named(client, "Aria")["id"]
    assert client.post(f"/api/heroes/{aria}/move", json={"team_id": beta}, headers=headers).status_code == 422, "full"
    ann = sign_in(client, "Ann")
    annas = save_team(client, ann, name="Annas", heroes=("Eve",)).json()["id"]
    eve = hero_named(client, "Eve")["id"]
    assert client.post(f"/api/heroes/{eve}/move", json={"team_id": alpha}, headers=ann).status_code == 404, "not hers"
    headers = log_in(client, "Mike")
    assert client.post(f"/api/heroes/{aria}/move", json={"team_id": annas}, headers=headers).status_code == 404, "not his"


def test_the_games_roster_rule_decides_and_its_reason_is_shown(mike, monkeypatch):
    from terraforma.heroes.hooks import Roster

    async def costs_tokens(self, session, account, hero, to_team):
        return "that costs 5 Challenge Tokens"

    client, headers = mike
    monkeypatch.setattr(Roster, "may_move", costs_tokens)
    alpha = save_team(client, headers, name="Alpha", heroes=("Aria", "Bob")).json()["id"]
    beta = save_team(client, headers, name="Beta", heroes=("Cato",)).json()["id"]
    answer = client.post(f"/api/heroes/{hero_named(client, 'Aria')['id']}/move", json={"team_id": beta}, headers=headers)
    assert (answer.status_code, answer.json()["detail"]) == (422, "that costs 5 Challenge Tokens")
    assert members_of(client, alpha) == [(0, "Aria"), (1, "Bob")]


def test_team_names_and_ownership(mike):
    client, headers = mike
    assert save_team(client, headers).status_code == 201
    assert save_team(client, headers, name="alpha", heroes=("Bob",)).status_code == 409
    team = client.get("/api/teams").json()[0]["id"]
    other = sign_in(client, "Ann")
    assert client.post(f"/api/teams/{team}/rename", json={"name": "Mine"}, headers=other).status_code == 404
    assert client.post(f"/api/teams/{team}/delete", headers=other).status_code == 404
    headers = log_in(client, "Mike")
    assert client.post(f"/api/teams/{team}/rename", json={"name": "Renamed"}, headers=headers).json()["name"] == "Renamed"


def test_deleting_a_team_deletes_its_heroes_unless_the_game_forbids_it(mike, monkeypatch):
    from terraforma.heroes.hooks import Roster

    client, headers = mike
    team = save_team(client, headers, heroes=("Aria", "Bob")).json()["id"]

    async def counts(db):
        return await db.scalar(select(func.count()).select_from(TeamMember)), await db.scalar(select(func.count()).select_from(Hero))

    async def no(self, session, account, team):
        return "teams stay"

    with monkeypatch.context() as patch:
        patch.setattr(Roster, "may_disband", no)
        refused = client.post(f"/api/teams/{team}/delete", headers=headers)
        assert (refused.status_code, refused.json()["detail"]) == (422, "teams stay")
        assert in_app_db(client, counts) == (2, 2)
    assert client.post(f"/api/teams/{team}/delete", headers=headers).status_code == 200
    assert in_app_db(client, counts) == (0, 0), "a hero does not exist without a team"
    assert client.post(f"/api/teams/{team}/delete", headers=headers).status_code == 404
