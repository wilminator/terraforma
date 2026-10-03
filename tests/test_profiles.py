"""Profiles: a player's, a team's and an alliance's page, each at its own random token, and who sees what."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account, set_handle
from terraforma.alliances.models import Alliance
from terraforma.game import Game
from terraforma.profiles.models import AllianceProfile, PlayerProfile, TeamProfile
from terraforma.testing import in_app_db

from .helpers import expect

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


async def _make(db, username):
    account = await create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True)
    await set_handle(db, account, f"{username}the{'Bold'}")


def sign_in(client, username):
    in_app_db(client, lambda db: _make(db, username))
    return login(client, username)


def login(client, username):
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def call(client, method, who, url, body=None, status=200):
    headers = login(client, who)
    response = client.request(method, url, json=body, headers=headers) if body is not None else client.request(method, url, headers=headers)
    return expect(response, status).json()


def public(client, url, status=200):
    """A call with no login at all."""
    client.cookies.clear()
    return expect(client.get(url), status).json()


@pytest.fixture
def pact(app_client):
    """Mike's Vanguard and Rivals; Zed's Strangers; Mike's Vanguard and Zed's Strangers are in the Iron Pact."""
    sign_in(app_client, "Mike")
    sign_in(app_client, "Zed")
    sign_in(app_client, "Yan")
    ids = {}
    for who, name in (("Mike", "Vanguard"), ("Mike", "Rivals"), ("Zed", "Strangers")):
        ids[name] = call(app_client, "POST", who, "/api/teams", {"name": name}, 201)["id"]
    ids["alliance"] = call(app_client, "POST", "Mike", "/api/alliances", {"team_id": ids["Vanguard"], "name": "Iron Pact"}, 201)["id"]
    call(app_client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/invite", {"team_id": ids["Vanguard"], "target_team_id": ids["Strangers"]})
    call(app_client, "POST", "Zed", f"/api/teams/{ids['Strangers']}/invitations/accept", {"alliance_id": ids["alliance"]})
    return app_client, ids


def teams_of(mine):
    return {team["name"]: team for team in mine["teams"]}


def test_there_is_no_page_until_the_player_makes_one(pact):
    client, _ = pact
    mine = call(client, "GET", "Mike", "/api/profile")
    assert mine["token"] is None and mine["bio"] == "" and mine["handle"] == "MiketheBold" and mine["directory"] is False
    assert [(team["name"], team["visible"]) for team in mine["teams"]] == [("Vanguard", False), ("Rivals", False)]
    public(client, "/api/p/doesnotexist1234", 404)


def test_the_players_page_shows_the_handle_the_bio_and_the_visible_teams_and_nothing_else(pact):
    client, ids = pact
    token = call(client, "POST", "Mike", "/api/profile/token")["token"]
    call(client, "PUT", "Mike", "/api/profile/bio", {"bio": "  Hello, travellers.  "})
    assert public(client, f"/api/p/{token}")["teams"] == []  # every team is hidden until shown
    mine = call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": True})
    page = public(client, f"/api/p/{token}")  # no login
    assert page == {"handle": "MiketheBold", "bio": "Hello, travellers.", "teams": [{"name": "Vanguard", "page": teams_of(mine)["Vanguard"]["token"]}]}
    assert "mike" not in json.dumps(page).lower().replace("miketheb", "")  # not the username or email


def test_a_new_token_replaces_the_old_for_good(pact):
    client, _ = pact
    old = call(client, "POST", "Mike", "/api/profile/token")["token"]
    new = call(client, "POST", "Mike", "/api/profile/token")["token"]
    assert new != old and len(new) >= 16
    public(client, f"/api/p/{old}", 404)
    assert public(client, f"/api/p/{new}")["handle"] == "MiketheBold"


def test_a_team_page_exists_only_while_the_team_is_visible_and_names_its_alliances(pact):
    client, ids = pact
    call(client, "POST", "Mike", "/api/profile/token")
    team_token = teams_of(call(client, "GET", "Mike", "/api/profile"))["Vanguard"]["token"]
    public(client, f"/api/p/team/{team_token}", 404)  # hidden
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": True})
    # The alliance has no page yet, so the team page names it with no link. The handle waits on the directory.
    assert public(client, f"/api/p/team/{team_token}") == {"name": "Vanguard", "alliances": [{"name": "Iron Pact", "page": None}]}
    alliance_token = call(client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/profile/token")["token"]
    assert public(client, f"/api/p/team/{team_token}")["alliances"] == [{"name": "Iron Pact", "page": alliance_token}]
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": False})
    public(client, f"/api/p/team/{team_token}", 404)


def test_the_directory_opt_in_adds_the_handle_and_takes_only_a_boolean(pact):
    client, ids = pact
    assert call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": True})["token"] is not None  # makes the page
    mine = call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": True})
    team_token = teams_of(mine)["Vanguard"]["token"]
    assert public(client, f"/api/p/team/{team_token}")["handle"] == "MiketheBold"
    assert call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": False})["directory"] is False
    assert "handle" not in public(client, f"/api/p/team/{team_token}")
    call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": "yes"}, 422)
    call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": True, "extra": 1}, 422)


def test_an_alliance_page_lists_only_visible_teams_and_names_a_player_only_in_the_directory(pact):
    client, ids = pact
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": True})
    token = call(client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/profile/token")["token"]
    page = public(client, f"/api/p/alliance/{token}")
    assert (page["name"], page["bio"]) == ("Iron Pact", "")
    assert [(team["name"], team["role"], "handle" in team) for team in page["teams"]] == [("Vanguard", "leader", False)]  # Strangers is hidden
    call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": True})
    assert public(client, f"/api/p/alliance/{token}")["teams"][0]["handle"] == "MiketheBold"
    call(client, "PUT", "Zed", "/api/profile/team-visible", {"team_id": ids["Strangers"], "visible": True})
    assert [team["name"] for team in public(client, f"/api/p/alliance/{token}")["teams"]] == ["Vanguard", "Strangers"]
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": False})
    assert [team["name"] for team in public(client, f"/api/p/alliance/{token}")["teams"]] == ["Strangers"]  # hidden appears nowhere


def test_members_open_a_visible_member_team_and_reach_the_player_only_in_the_directory(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/teams/{ids['Vanguard']}/profile"
    call(client, "GET", "Zed", url, status=404)  # Mike has made nothing visible
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Vanguard"], "visible": True})
    assert call(client, "GET", "Zed", url) == {"name": "Vanguard", "alliances": [{"name": "Iron Pact", "page": None}]}
    token = call(client, "PUT", "Mike", "/api/profile/directory", {"enabled": True})["token"]
    assert call(client, "GET", "Zed", url) == {"name": "Vanguard", "handle": "MiketheBold", "player": token, "alliances": [{"name": "Iron Pact", "page": None}]}
    call(client, "GET", "Yan", url, status=404)  # not in the alliance: it does not exist to Yan
    call(client, "GET", "Zed", f"/api/alliances/{ids['alliance']}/teams/{ids['Rivals']}/profile", status=404)  # Rivals is not in it


def test_only_a_role_that_may_speak_writes_the_alliance_page(pact):
    client, ids = pact
    call(client, "GET", "Yan", f"/api/alliances/{ids['alliance']}/profile", status=404)
    call(client, "PUT", "Yan", f"/api/alliances/{ids['alliance']}/profile/bio", {"bio": "x"}, 404)
    assert call(client, "GET", "Zed", f"/api/alliances/{ids['alliance']}/profile")["token"] is None  # a member can read
    call(client, "PUT", "Mike", f"/api/alliances/{ids['alliance']}/profile/bio", {"bio": "We hold the ford."})
    assert call(client, "GET", "Zed", f"/api/alliances/{ids['alliance']}/profile")["bio"] == "We hold the ford."


def test_a_player_shows_one_team_and_gives_it_a_new_address_and_cannot_touch_anothers(pact):
    client, ids = pact
    call(client, "POST", "Mike", "/api/profile/token")
    before = {team["name"]: team["token"] for team in call(client, "GET", "Mike", "/api/profile")["teams"]}
    after = call(client, "POST", "Mike", "/api/profile/team-token", {"team_id": ids["Vanguard"]})
    assert {team["name"]: team["token"] for team in after["teams"]}["Vanguard"] != before["Vanguard"]
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": ids["Strangers"], "visible": True}, 404)  # Zed's
    call(client, "POST", "Mike", "/api/profile/team-token", {"team_id": ids["Strangers"]}, 404)


def test_the_calls_take_only_what_they_define_and_a_long_bio_is_refused(pact):
    client, _ = pact
    call(client, "PUT", "Mike", "/api/profile/bio", {"bio": "x", "extra": 1}, 422)
    call(client, "PUT", "Mike", "/api/profile/bio", {"bio": "x" * 501}, 422)
    call(client, "PUT", "Mike", "/api/profile/bio", {"bio": "bad\x00text"}, 422)
    call(client, "PUT", "Mike", "/api/profile/team-visible", {"team_id": 1, "visible": "yes"}, 422)
    public(client, "/api/p/short", 422)  # not even shaped like a token


def test_changing_a_page_needs_a_login_and_the_csrf_token_and_the_public_pages_are_limited_by_address(pact, monkeypatch):
    client, _ = pact
    token = call(client, "POST", "Mike", "/api/profile/token")["token"]
    client.cookies.clear()
    expect(client.post("/api/profile/token"), 401)
    expect(client.get("/api/profile"), 401)
    login(client, "Mike")  # logged in, but no CSRF header
    expect(client.put("/api/profile/bio", json={"bio": "x"}), 403)
    monkeypatch.setattr(ratelimit, "PUBLIC_PROFILE_BY_ADDRESS", ratelimit.Limit("profile-public", 2, 60))
    public(client, f"/api/p/{token}")
    public(client, f"/api/p/{token}")
    public(client, f"/api/p/{token}", 429)


def test_deleting_a_team_takes_its_page_with_it(pact):
    client, ids = pact
    call(client, "POST", "Mike", "/api/profile/token")
    call(client, "POST", "Mike", f"/api/alliances/{ids['alliance']}/profile/token")
    call(client, "POST", "Mike", f"/api/teams/{ids['Rivals']}/delete")

    async def counts(db):
        return [await db.scalar(select(func.count()).select_from(table)) for table in (PlayerProfile, TeamProfile, AllianceProfile, Alliance)]

    assert in_app_db(client, counts) == [1, 1, 1, 1]  # Vanguard's row stays; Rivals' is gone
