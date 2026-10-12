"""Following, profile bans and the directory: one way, private to the follower, capped by play time. On every database."""

import json

import pytest
from sqlalchemy import update

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account, set_handle
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.playtime.models import PlayTime
from terraforma.testing import in_app_db

from .helpers import expect

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}


# --- the rule (pure) ---------------------------------------------------------------------------------------------------

def test_a_player_may_follow_ten_and_one_more_for_every_hour_played():
    rules = Rules()
    assert [rules.follow_cap(seconds) for seconds in (0, 3599, 3600, 7200, 36000)] == [10, 10, 11, 12, 20]


def test_a_game_sets_the_follow_cap():
    class Tight(Rules):
        follow_base = 3
        follow_seconds_each = 60

    assert [Tight().follow_cap(seconds) for seconds in (0, 59, 60, 600)] == [3, 3, 4, 13]


# --- the calls ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, rules=Rules())


async def _make(db, username, handle):
    account = await create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True)
    await set_handle(db, account, handle)


def player(client, username, handle=None):
    in_app_db(client, lambda db: _make(db, username, handle or f"{username} Bold"))


def login(client, username):
    return {"X-CSRF-Token": expect(client.post("/api/login", json={"username": username, "password": PASSWORD}), 200).json()["csrf_token"]}


def call(client, who, method, url, body=None, status=200):
    headers = login(client, who)
    response = client.request(method, url, json=body, headers=headers) if body is not None else client.request(method, url, headers=headers)
    return expect(response, status).json()


@pytest.fixture
def crowd(app_client):
    for name in ("Mike", "Ann", "Bob", "Cyd", "Dee"):
        player(app_client, name)
    return app_client


def follows(client, who):
    return call(client, who, "GET", "/api/follows")


def handles(listing):
    return [entry["handle"] for entry in listing["following"]]


def test_the_follow_calls_need_a_login_and_the_changing_ones_a_csrf_token(crowd):
    crowd.cookies.clear()
    for method, url in (("GET", "/api/follows"), ("GET", "/api/followers"), ("GET", "/api/bans"), ("GET", "/api/directory?prefix=a")):
        assert crowd.request(method, url).status_code == 401, url
    login(crowd, "Mike")
    for url in ("/api/follows", "/api/follows/remove", "/api/bans", "/api/bans/remove"):
        assert crowd.post(url, json={"handle": "Ann Bold"}).status_code == 403, url
    assert crowd.put("/api/follows/share-back", json={"handle": "Ann Bold", "enabled": True}).status_code == 403


def test_following_is_instant_one_way_and_private_and_the_followed_only_sees_a_count(crowd):
    answer = call(crowd, "Mike", "POST", "/api/follows", {"handle": "ann  BOLD"})  # case and spacing do not matter
    assert handles(answer) == ["Ann Bold"] and answer["followers"] == 0
    assert handles(follows(crowd, "Ann")) == [], "one way: Ann does not follow Mike"
    assert follows(crowd, "Ann")["followers"] == 1
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 1, "sharing": []}, "no names"
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})  # following again changes nothing
    assert follows(crowd, "Ann")["followers"] == 1
    assert handles(call(crowd, "Mike", "POST", "/api/follows/remove", {"handle": "Ann Bold"})) == []
    assert follows(crowd, "Ann")["followers"] == 0


def test_you_cannot_follow_yourself_or_a_player_who_does_not_exist(crowd):
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Miketheo"}, 404)
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Mike Bold"}, 422)
    call(crowd, "Mike", "POST", "/api/follows/remove", {"handle": "Nobody"}, 404)
    assert handles(follows(crowd, "Mike")) == []


def test_the_follow_list_is_capped_and_play_time_raises_the_cap(app_client, tmp_path):
    class Tight(Rules):
        follow_base = 2

    app_client.app.state.game = Game(name="Test Game", rules=Tight())
    for name in ("Mike", "Ann", "Bob", "Cyd"):
        player(app_client, name)
    assert follows(app_client, "Mike")["cap"] == 2
    call(app_client, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    call(app_client, "Mike", "POST", "/api/follows", {"handle": "Bob Bold"})
    refused = call(app_client, "Mike", "POST", "/api/follows", {"handle": "Cyd Bold"}, 422)
    assert "2" in refused["detail"] and "longer" in refused["detail"]
    call(app_client, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})  # one already followed is no new follow

    async def played_an_hour(db):
        await db.execute(update(PlayTime).values(seconds=3600, counted_to=PlayTime.counted_to + 3600))

    in_app_db(app_client, played_an_hour)
    assert follows(app_client, "Mike")["cap"] == 3
    assert len(handles(call(app_client, "Mike", "POST", "/api/follows", {"handle": "Cyd Bold"}))) == 3


def test_the_list_puts_the_most_recently_active_first_and_players_who_never_played_last(crowd, later):
    for who in ("Ann", "Bob", "Cyd"):
        call(crowd, "Mike", "POST", "/api/follows", {"handle": f"{who} Bold"})
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Dee Bold"})
    later(100)
    call(crowd, "Bob", "POST", "/api/bans/remove", {"handle": "Cyd Bold"})  # any call that changes something is activity
    later(200)
    call(crowd, "Ann", "POST", "/api/bans/remove", {"handle": "Cyd Bold"})
    later(300)
    call(crowd, "Cyd", "POST", "/api/bans/remove", {"handle": "Ann Bold"})
    assert handles(follows(crowd, "Mike")) == ["Cyd Bold", "Ann Bold", "Bob Bold", "Dee Bold"]
    later(400)
    call(crowd, "Bob", "POST", "/api/bans/remove", {"handle": "Cyd Bold"})
    assert handles(follows(crowd, "Mike"))[0] == "Bob Bold"


def test_sharing_back_is_off_until_the_follower_switches_it_on_and_per_follow(crowd):
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Bob Bold"})
    assert call(crowd, "Ann", "GET", "/api/followers")["sharing"] == []
    answer = call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Ann Bold", "enabled": True})
    assert [(entry["handle"], entry["share_back"]) for entry in answer["following"]] == [("Ann Bold", True), ("Bob Bold", False)]
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 1, "sharing": [{"handle": "Mike Bold", "profile": None}]}
    assert call(crowd, "Bob", "GET", "/api/followers")["sharing"] == []
    token = call(crowd, "Mike", "POST", "/api/profile/token")["token"]
    assert call(crowd, "Ann", "GET", "/api/followers")["sharing"] == [{"handle": "Mike Bold", "profile": token}], "the profile link, once there is a page"
    call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Ann Bold", "enabled": False})
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 1, "sharing": []}
    call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Cyd Bold", "enabled": True}, 404)


def test_unfollowing_ends_the_sharing_and_following_again_starts_it_off(crowd):
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Ann Bold", "enabled": True})
    call(crowd, "Mike", "POST", "/api/follows/remove", {"handle": "Ann Bold"})
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 0, "sharing": []}
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 1, "sharing": []}


def test_a_ban_overrides_the_follow_takes_back_the_sharing_and_changes_nothing_the_banned_player_can_see(crowd):
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Ann Bold", "enabled": True})
    assert call(crowd, "Ann", "POST", "/api/bans", {"handle": "Mike Bold"}) == {"banned": ["Mike Bold"]}
    assert call(crowd, "Ann", "GET", "/api/bans") == {"banned": ["Mike Bold"]}
    assert call(crowd, "Ann", "GET", "/api/followers") == {"count": 0, "sharing": []}, "the follow counts for nothing"
    mine = follows(crowd, "Mike")
    assert handles(mine) == ["Ann Bold"], "and Mike is not told: it is still on his list"
    assert [entry["share_back"] for entry in mine["following"]] == [False], "but his sharing was taken back"
    call(crowd, "Mike", "PUT", "/api/follows/share-back", {"handle": "Ann Bold", "enabled": True})
    assert call(crowd, "Ann", "GET", "/api/followers")["sharing"] == [], "switching it on again shows nothing while he is banned"
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Bob Bold"})  # a ban is Ann's alone: Mike follows others as before
    assert call(crowd, "Ann", "POST", "/api/bans/remove", {"handle": "Mike Bold"}) == {"banned": []}
    assert call(crowd, "Ann", "GET", "/api/followers")["count"] == 1, "lifted: the follow counts again"


def test_you_cannot_ban_yourself_or_a_player_who_does_not_exist(crowd):
    call(crowd, "Mike", "POST", "/api/bans", {"handle": "Mike Bold"}, 422)
    call(crowd, "Mike", "POST", "/api/bans", {"handle": "Nobody"}, 404)


def test_a_ban_hides_the_profile_pages_from_the_banned_player_only_when_they_are_logged_in(crowd):
    token = call(crowd, "Ann", "POST", "/api/profile/token")["token"]
    team = call(crowd, "Ann", "POST", "/api/teams", {"name": "Vanguard", "heroes": [{"name": "Ann hero", "job": "fighter"}]}, 201)["id"]
    call(crowd, "Ann", "POST", "/api/profile/token")
    call(crowd, "Ann", "PUT", "/api/profile/team-visible", {"team_id": team, "visible": True})
    mine = call(crowd, "Ann", "GET", "/api/profile")
    token, team_token = mine["token"], mine["teams"][0]["token"]
    call(crowd, "Ann", "POST", "/api/bans", {"handle": "Mike Bold"})
    crowd.cookies.clear()
    expect(crowd.get(f"/api/p/{token}"), 200)  # anyone with the link and no login
    login(crowd, "Bob")
    expect(crowd.get(f"/api/p/{token}"), 200)
    expect(crowd.get(f"/api/p/team/{team_token}"), 200)
    login(crowd, "Mike")
    expect(crowd.get(f"/api/p/{token}"), 404)
    expect(crowd.get(f"/api/p/team/{team_token}"), 404)
    login(crowd, "Ann")
    call(crowd, "Ann", "POST", "/api/bans/remove", {"handle": "Mike Bold"})
    login(crowd, "Mike")
    expect(crowd.get(f"/api/p/{token}"), 200)


# --- the directory -----------------------------------------------------------------------------------------------------

def opt_in(client, who, bio="", link=False):
    call(client, who, "PUT", "/api/profile/bio", {"bio": bio})
    call(client, who, "PUT", "/api/profile/directory", {"enabled": True})
    if link:
        call(client, who, "PUT", "/api/profile/directory-link", {"enabled": True})


def search(client, who, prefix, status=200):
    return call(client, who, "GET", f"/api/directory?prefix={prefix}", status=status)


def test_the_directory_lists_only_players_who_opted_in_by_the_start_of_their_handle(crowd):
    assert search(crowd, "Mike", "A") == {"players": []}, "nobody opted in"
    opt_in(crowd, "Ann", "Hello there")
    opt_in(crowd, "Bob")
    call(crowd, "Cyd", "PUT", "/api/profile/bio", {"bio": "a page but not in the directory"})
    assert search(crowd, "Mike", "a")["players"] == [{"handle": "Ann Bold", "bio": "Hello there", "following": False, "profile": None}]
    assert [entry["handle"] for entry in search(crowd, "Mike", "BOB")["players"]] == ["Bob Bold"], "case does not matter"
    assert [entry["handle"] for entry in search(crowd, "Mike", "Ann%20B")["players"]] == ["Ann Bold"], "nor does the spacing in a handle"
    assert search(crowd, "Mike", "the")["players"] == [], "the start of a handle, not the middle"
    assert search(crowd, "Mike", "Cyd")["players"] == []


def test_the_directory_does_not_list_the_searcher_or_players_who_banned_them_and_marks_those_they_follow(crowd):
    opt_in(crowd, "Mike")
    opt_in(crowd, "Ann")
    opt_in(crowd, "Bob")
    assert [entry["handle"] for entry in search(crowd, "Ann", "Mike")["players"]] == ["Mike Bold"]
    assert search(crowd, "Mike", "Mike")["players"] == [], "not oneself"
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Bob Bold"})
    assert search(crowd, "Mike", "Bob")["players"][0]["following"] is True
    call(crowd, "Ann", "POST", "/api/bans", {"handle": "Mike Bold"})
    assert search(crowd, "Mike", "Ann")["players"] == []


def test_the_profile_link_is_in_a_result_only_when_the_player_turned_it_on(crowd):
    opt_in(crowd, "Ann")
    token = call(crowd, "Ann", "GET", "/api/profile")["token"]
    assert search(crowd, "Mike", "Ann")["players"][0]["profile"] is None
    answer = call(crowd, "Ann", "PUT", "/api/profile/directory-link", {"enabled": True})
    assert answer["directory_link"] is True
    assert search(crowd, "Mike", "Ann")["players"][0]["profile"] == token
    call(crowd, "Mike", "POST", "/api/follows", {"handle": "Ann Bold"})
    assert follows(crowd, "Mike")["following"][0]["profile"] == token, "the follow list shows it too"
    call(crowd, "Ann", "PUT", "/api/profile/directory-link", {"enabled": False})
    assert search(crowd, "Mike", "Ann")["players"][0]["profile"] is None
    assert follows(crowd, "Mike")["following"][0]["profile"] is None


def test_a_prefix_is_text_not_a_pattern(crowd):
    for name, handle in (("Eve", "Eve_x"), ("Fay", "Evexa")):
        player(crowd, name, handle)
        opt_in(crowd, name)
    assert [entry["handle"] for entry in search(crowd, "Mike", "Eve_")["players"]] == ["Eve_x"], "an underscore is not a wildcard"
    assert search(crowd, "Mike", "%25")["players"] == []


def test_a_search_gives_at_most_twenty_players(crowd):
    from terraforma.profiles import service as profiles

    async def sign_up(db):
        for number in range(23):
            account = await create_account(db, f"Pat{number}", PASSWORD, email=f"pat{number}@example.com", confirmed=True)
            await set_handle(db, account, f"Pal {number:02}")
            await profiles.set_directory(db, account, True)

    in_app_db(crowd, sign_up)
    found = [entry["handle"] for entry in search(crowd, "Mike", "pal")["players"]]
    assert len(found) == 20 and found == sorted(found)


def test_the_directory_is_rate_limited_and_needs_a_prefix(crowd):
    search(crowd, "Mike", "", 422)
    headers = login(crowd, "Mike")
    for _ in range(ratelimit.DIRECTORY_BY_ACCOUNT.attempts):
        expect(crowd.get("/api/directory?prefix=a", headers=headers), 200)
    refused = expect(crowd.get("/api/directory?prefix=a", headers=headers), 429)
    assert int(refused.headers["Retry-After"]) > 0


def test_a_call_of_the_wrong_shape_is_refused(crowd):
    headers = login(crowd, "Mike")
    assert crowd.post("/api/follows", json={"handle": ""}, headers=headers).status_code == 422
    assert crowd.post("/api/follows", json={"handle": "x" * 25}, headers=headers).status_code == 422
    assert crowd.post("/api/follows", json={"handle": "Ann", "extra": 1}, headers=headers).status_code == 422
    assert crowd.put("/api/follows/share-back", json={"handle": "Ann", "enabled": "yes"}, headers=headers).status_code == 422
