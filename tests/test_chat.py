"""Chat: channels of every kind through one framework, saying and reading, muting, flagging, the admin's flags and retention."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from terraforma import housekeeping, wallclock
from terraforma.accounts.service import create_account, set_handle
from terraforma.chat.hooks import Channel, ChannelKind, Chat
from terraforma.chat.models import ChatFlag, ChatMessage
from terraforma.game import Game
from terraforma.heroes.models import Hero
from terraforma.models import Account
from terraforma.parties import service as parties
from terraforma.parties.models import PartyTeam
from terraforma.settings import Settings
from terraforma.testing import in_app_db
from terraforma.towns.hooks import Towns

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
    """Signs in (once, until another player takes the session over: logins are rate-limited) and gives the CSRF header."""
    if getattr(client, "chat_user", None) != username:
        client.chat_headers = {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}
        client.chat_user = username
    return client.chat_headers


def call(client, method, who, url, body=None, status=200):
    headers = login(client, who)
    response = client.request(method, url, json=body, headers=headers) if body is not None else client.request(method, url, headers=headers)
    return expect(response, status).json()


def say(client, who, kind, key, body, status=201):
    return call(client, "POST", who, "/api/chat/say", {"kind": kind, "key": key, "body": body}, status)


def hear(client, who, kind, key, after=0, status=200):
    answer = call(client, "GET", who, f"/api/chat/messages?kind={kind}&key={key}&after={after}", None, status)
    return answer["messages"] if status == 200 else answer


def bodies(lines):
    return [each["body"] for each in lines]


@pytest.fixture
def players(app_client):
    """Mike (Vanguard, in the Iron Pact), Zed (Strangers, in the Iron Pact) and Yan (Loner), each with a team of one hero."""
    for name in ("Mike", "Zed", "Yan"):
        sign_in(app_client, name)
    ids = {}
    for who, team in (("Mike", "Vanguard"), ("Zed", "Strangers"), ("Yan", "Loner")):
        ids[team] = call(app_client, "POST", who, "/api/teams", {"name": team, "heroes": [{"name": f"{team} hero", "job": "fighter"}]}, 201)["id"]
    pact = call(app_client, "POST", "Mike", "/api/alliances", {"team_id": ids["Vanguard"], "name": "Iron Pact"}, 201)["id"]
    call(app_client, "POST", "Mike", f"/api/alliances/{pact}/invite", {"team_id": ids["Vanguard"], "target_team_id": ids["Strangers"]})
    call(app_client, "POST", "Zed", f"/api/teams/{ids['Strangers']}/invitations/accept", {"alliance_id": pact})
    ids["alliance"] = pact
    return app_client, ids


def one_party(client, ids):
    """Mike's and Zed's teams in one party; returns its id."""

    async def work(db):
        party = await parties.create_party(db, ids["Vanguard"], 20)
        db.add(PartyTeam(party_id=party.id, team_id=ids["Strangers"], position=1))
        return party.id

    return in_app_db(client, work)


def move_hero(client, team_name, x):
    async def work(db):
        await db.execute(update(Hero).where(Hero.name == f"{team_name} hero").values(x=x))

    in_app_db(client, work)


# --- the channel list: one answer for every kind -----------------------------------------------------------------------------

def test_a_player_with_no_party_or_alliance_is_in_the_town_the_nearby_and_the_global_channel(app_client):
    sign_in(app_client, "Mike")
    call(app_client, "POST", "Mike", "/api/teams", {"name": "Vanguard", "heroes": [{"name": "Aria", "job": "fighter"}]}, 201)
    kinds = [each["kind"] for each in call(app_client, "GET", "Mike", "/api/chat/channels")["channels"]]
    assert kinds == ["nearby", "town", "global"]


def test_party_and_alliance_channels_appear_with_the_party_and_the_alliance(players):
    client, ids = players
    assert [each["kind"] for each in call(client, "GET", "Yan", "/api/chat/channels")["channels"]] == ["nearby", "town", "global"]
    party = one_party(client, ids)
    channels = call(client, "GET", "Mike", "/api/chat/channels")["channels"]
    assert {"kind": "party", "key": str(party), "label": "Party"} in channels
    assert {"kind": "alliance", "key": str(ids["alliance"]), "label": "Iron Pact"} in channels
    assert not [each for each in call(client, "GET", "Yan", "/api/chat/channels")["channels"] if each["kind"] in ("party", "alliance")]


def test_chat_needs_a_login(app_client):
    expect(app_client.get("/api/chat/channels"), 401)
    expect(app_client.post("/api/chat/say", json={"kind": "global", "key": "all", "body": "hi"}), 401)


# --- saying and reading ---------------------------------------------------------------------------------------------------------

def test_what_one_player_says_everyone_in_the_channel_reads_in_order_with_the_handle_only(players):
    client, _ = players
    say(client, "Mike", "global", "all", "Hello,   world")
    say(client, "Zed", "global", "all", "Hi Mike")
    seen = hear(client, "Yan", "global", "all")
    assert bodies(seen) == ["Hello, world", "Hi Mike"], "white space is collapsed"
    assert [each["sender"] for each in seen] == ["Mikethe" + "Bold", "Zedthe" + "Bold"]
    assert set(seen[0]) == {"id", "sender", "body", "at", "mine"}, "no account id, username or email"
    assert [each["mine"] for each in hear(client, "Mike", "global", "all")] == [True, False]


def test_a_page_asks_for_the_lines_after_the_last_one_it_has(players):
    client, _ = players
    first = say(client, "Mike", "global", "all", "one")["id"]
    say(client, "Mike", "global", "all", "two")
    say(client, "Mike", "global", "all", "three")
    assert bodies(hear(client, "Zed", "global", "all", after=first)) == ["two", "three"]
    assert hear(client, "Zed", "global", "all", after=10_000) == []


def test_a_first_read_brings_the_latest_lines_oldest_first(players):
    client, _ = players
    for number in range(8):
        say(client, "Mike", "global", "all", f"line {number}")
    latest = call(client, "GET", "Zed", "/api/chat/messages?kind=global&key=all&limit=3")["messages"]
    assert bodies(latest) == ["line 5", "line 6", "line 7"]


def test_a_message_must_say_something_and_not_too_much(players):
    client, _ = players
    say(client, "Mike", "global", "all", "   \n ", 409)
    say(client, "Mike", "global", "all", "x" * 501, 409)
    say(client, "Mike", "global", "all", "x" * 500)
    say(client, "Mike", "global", "all", "x" * 2001, 422)
    say(client, "Mike", "global", "all", "hi", 201)
    expect(call_raw(client, "Mike", {"kind": "global", "key": "all", "body": "hi", "extra": 1}), 422)


def call_raw(client, who, body):
    return client.post("/api/chat/say", json=body, headers=login(client, who))


def test_a_player_without_a_handle_cannot_chat(app_client):
    async def bare(db):
        await create_account(db, "Quiet", PASSWORD, email="quiet@example.com", confirmed=True)

    in_app_db(app_client, bare)
    assert "handle" in say(app_client, "Quiet", "global", "all", "hi", 409)["detail"]


def test_an_unknown_kind_is_not_found_and_a_channel_you_are_not_in_is_refused(players):
    client, ids = players
    say(client, "Mike", "nonsense", "x", "hi", 404)
    party = one_party(client, ids)
    assert "not in that channel" in say(client, "Yan", "party", str(party), "let me in", 409)["detail"]
    hear(client, "Yan", "party", str(party), status=409)
    hear(client, "Yan", "alliance", str(ids["alliance"]), status=409)
    hear(client, "Yan", "global", "elsewhere", status=409)
    hear(client, "Yan", "party", "not-a-number", status=409)


# --- party and alliance ----------------------------------------------------------------------------------------------------------

def test_party_chat_is_for_the_players_with_a_team_in_the_party(players):
    client, ids = players
    party = str(one_party(client, ids))
    say(client, "Mike", "party", party, "plan?")
    assert bodies(hear(client, "Zed", "party", party)) == ["plan?"]
    hear(client, "Yan", "party", party, status=409)


def test_alliance_chat_is_for_the_players_with_a_team_in_the_alliance(players):
    client, ids = players
    alliance = str(ids["alliance"])
    say(client, "Zed", "alliance", alliance, "pact business")
    assert bodies(hear(client, "Mike", "alliance", alliance)) == ["pact business"]
    say(client, "Yan", "alliance", alliance, "me too", 409)


# --- whispers ---------------------------------------------------------------------------------------------------------------------

def test_a_whisper_goes_by_handle_and_is_one_channel_from_both_sides(players):
    client, _ = players
    say(client, "Mike", "whisper", "Zedthe" + "Bold", "psst")
    say(client, "Zed", "whisper", "miketheBOLD", "yes?")
    assert bodies(hear(client, "Mike", "whisper", "Zedthe" + "Bold")) == ["psst", "yes?"]
    assert bodies(hear(client, "Zed", "whisper", "Mikethe" + "Bold")) == ["psst", "yes?"]
    assert hear(client, "Yan", "whisper", "Mikethe" + "Bold") == [], "Yan's whisper channel with Mike is a different, empty one"
    listed = [each for each in call(client, "GET", "Zed", "/api/chat/channels")["channels"] if each["kind"] == "whisper"]
    assert [each["label"] for each in listed] == ["Mikethe" + "Bold"]
    assert bodies(hear(client, "Zed", "whisper", listed[0]["key"])) == ["psst", "yes?"], "the listed key works as it stands"


def test_a_whisper_to_nobody_or_to_yourself_is_not_found(players):
    client, _ = players
    say(client, "Mike", "whisper", "Nobody Here", "hello?", 404)
    say(client, "Mike", "whisper", "Mikethe" + "Bold", "me", 404)


# --- nearby and town --------------------------------------------------------------------------------------------------------------

def nearby_key(client, who):
    return next(each["key"] for each in call(client, "GET", who, "/api/chat/channels")["channels"] if each["kind"] == "nearby")


def test_nearby_is_heard_within_range_only(players):
    client, _ = players
    key = nearby_key(client, "Mike")
    move_hero(client, "Strangers", 3)
    move_hero(client, "Loner", 40)
    say(client, "Mike", "nearby", key, "anyone close?")
    assert bodies(hear(client, "Zed", "nearby", key)) == ["anyone close?"]
    assert hear(client, "Yan", "nearby", key) == [], "same map, too far away"


def test_a_message_is_kept_with_the_place_it_was_said_from(players):
    client, _ = players
    say(client, "Mike", "global", "all", "here")

    async def look(db):
        message = await db.scalar(select(ChatMessage))
        hero = await db.scalar(select(Hero).where(Hero.name == "Vanguard hero"))
        return (message.map_id, message.x, message.y) == (hero.map_id, hero.x, hero.y)

    assert in_app_db(client, look)


def test_the_town_channel_is_for_whoever_stands_in_a_town(players, monkeypatch):
    client, _ = players
    key = next(each["key"] for each in call(client, "GET", "Mike", "/api/chat/channels")["channels"] if each["kind"] == "town")
    say(client, "Mike", "town", key, "welcome")
    assert bodies(hear(client, "Yan", "town", key)) == ["welcome"]

    async def nowhere(self, session, map_id, x, y):
        return False

    monkeypatch.setattr(Towns, "is_town", nowhere)
    assert not [each for each in call(client, "GET", "Yan", "/api/chat/channels")["channels"] if each["kind"] == "town"]
    hear(client, "Yan", "town", key, status=409)


# --- muting ----------------------------------------------------------------------------------------------------------------------

def test_a_muted_player_is_not_heard_until_unmuted_and_the_mute_is_private(players):
    client, _ = players
    say(client, "Zed", "global", "all", "noise")
    say(client, "Yan", "global", "all", "signal")
    assert call(client, "POST", "Mike", "/api/chat/mute", {"handle": "zedthebold"})["muted"] == ["Zedthe" + "Bold"]
    assert bodies(hear(client, "Mike", "global", "all")) == ["signal"]
    assert bodies(hear(client, "Yan", "global", "all")) == ["noise", "signal"], "others still hear Zed"
    assert call(client, "GET", "Mike", "/api/chat/mutes")["muted"] == ["Zedthe" + "Bold"]
    assert call(client, "POST", "Mike", "/api/chat/unmute", {"handle": "Zedthe" + "Bold"})["muted"] == []
    assert bodies(hear(client, "Mike", "global", "all")) == ["noise", "signal"]


def test_a_mute_hides_whispers_too_and_a_run_of_muted_lines_does_not_hide_the_rest(players):
    client, _ = players
    say(client, "Yan", "global", "all", "earlier")
    for number in range(6):
        say(client, "Zed", "global", "all", f"spam {number}")
    call(client, "POST", "Mike", "/api/chat/mute", {"handle": "Zedthe" + "Bold"})
    latest = call(client, "GET", "Mike", "/api/chat/messages?kind=global&key=all&limit=2")["messages"]
    assert bodies(latest) == ["earlier"], "the older line still shows when the newest are muted"
    say(client, "Zed", "whisper", "Mikethe" + "Bold", "hey")
    assert hear(client, "Mike", "whisper", "Zedthe" + "Bold") == []


def test_you_cannot_mute_yourself_or_nobody(players):
    client, _ = players
    call(client, "POST", "Mike", "/api/chat/mute", {"handle": "Mikethe" + "Bold"}, 409)
    call(client, "POST", "Mike", "/api/chat/mute", {"handle": "Nobody Here"}, 404)
    call(client, "POST", "Mike", "/api/chat/unmute", {"handle": "Nobody Here"})  # nothing to undo is fine


def test_muting_twice_is_one_mute(players):
    client, _ = players
    for _ in range(2):
        call(client, "POST", "Mike", "/api/chat/mute", {"handle": "Zedthe" + "Bold"})
    assert call(client, "GET", "Mike", "/api/chat/mutes")["muted"] == ["Zedthe" + "Bold"]


# --- flagging ---------------------------------------------------------------------------------------------------------------------

def flag(client, who, message_id, reason="tou", note="", status=201):
    return call(client, "POST", who, "/api/chat/flag", {"message_id": message_id, "reason": reason, "note": note}, status)


def make_admin(client, username):
    async def work(db):
        await db.execute(update(Account).where(Account.username == username).values(is_admin=True))

    in_app_db(client, work)


def test_a_player_flags_a_message_they_can_see_and_an_admin_reads_it_with_what_came_before(players):
    client, _ = players
    for text in ("a", "b", "c"):
        say(client, "Yan", "global", "all", text)
    bad = say(client, "Zed", "global", "all", "something against the terms")["id"]
    assert flag(client, "Mike", bad, note="  slur  ")["state"] == "open"
    make_admin(client, "Yan")
    [only] = call(client, "GET", "Yan", "/api/chat/admin/flags")["flags"]
    assert (only["reason"], only["note"], only["reporter"]) == ("tou", "slur", "Mikethe" + "Bold")
    assert only["message"]["body"] == "something against the terms" and only["message"]["sender"] == "Zedthe" + "Bold"
    assert bodies(only["before"]) == ["a", "b", "c"]


def test_flagging_twice_is_one_flag_and_your_own_message_cannot_be_flagged(players):
    client, _ = players
    bad = say(client, "Zed", "global", "all", "rude")["id"]
    assert flag(client, "Mike", bad)["id"] == flag(client, "Mike", bad)["id"]
    own = say(client, "Mike", "global", "all", "mine")["id"]
    assert "own" in flag(client, "Mike", own, status=409)["detail"]

    async def count(db):
        return await db.scalar(select(func.count()).select_from(ChatFlag))

    assert in_app_db(client, count) == 1


def test_the_reason_must_be_one_the_game_allows(players):
    client, _ = players
    bad = say(client, "Zed", "global", "all", "rude")["id"]
    assert flag(client, "Mike", bad, reason="boredom", status=409)["detail"].startswith("the reason is one of")
    assert call(client, "GET", "Mike", "/api/chat/reasons")["reasons"] == ["tou", "spam", "harassment", "other"]


def test_a_message_you_cannot_see_cannot_be_flagged(players):
    client, _ = players
    secret = say(client, "Mike", "whisper", "Zedthe" + "Bold", "private")["id"]
    flag(client, "Yan", secret, status=404)
    flag(client, "Mike", 9999, status=404)
    assert flag(client, "Zed", secret)["state"] == "open", "the one whispered to may report it"


def test_only_an_admin_reads_and_settles_flags(players):
    client, _ = players
    bad = say(client, "Zed", "global", "all", "rude")["id"]
    flag_id = flag(client, "Mike", bad)["id"]
    call(client, "GET", "Mike", "/api/chat/admin/flags", None, 403)
    call(client, "POST", "Mike", "/api/chat/admin/settle", {"flag_id": flag_id, "state": "actioned"}, 403)
    make_admin(client, "Yan")
    call(client, "POST", "Yan", "/api/chat/admin/settle", {"flag_id": flag_id, "state": "open"}, 422)
    call(client, "POST", "Yan", "/api/chat/admin/settle", {"flag_id": 999, "state": "actioned"}, 404)
    assert call(client, "POST", "Yan", "/api/chat/admin/settle", {"flag_id": flag_id, "state": "actioned"})["state"] == "actioned"
    assert call(client, "GET", "Yan", "/api/chat/admin/flags")["flags"] == []
    assert [each["id"] for each in call(client, "GET", "Yan", "/api/chat/admin/flags?state=actioned")["flags"]] == [flag_id]


# --- a game's say ------------------------------------------------------------------------------------------------------------------

def test_a_game_can_refuse_what_may_be_said(players, monkeypatch):
    client, _ = players

    async def no_links(self, session, speaker, kind, key, body):
        return "no links, please" if "http" in body else None

    monkeypatch.setattr(Chat, "may_speak", no_links)
    assert say(client, "Mike", "global", "all", "see http://x.test", 409)["detail"] == "no links, please"
    say(client, "Mike", "global", "all", "plain words")


def test_a_game_adds_a_kind_of_channel_without_touching_a_call(players, monkeypatch):
    client, _ = players

    class Guildhall(ChannelKind):
        name = "guild"

        async def member(self, session, game, account_id, key):
            return key == "smiths"

        async def channels(self, session, game, account_id):
            return [Channel(self.name, "smiths", "Smiths")]

    monkeypatch.setattr(Chat, "kinds", (*Chat.kinds, Guildhall()))
    assert {"kind": "guild", "key": "smiths", "label": "Smiths"} in call(client, "GET", "Mike", "/api/chat/channels")["channels"]
    say(client, "Mike", "guild", "smiths", "forge is lit")
    assert bodies(hear(client, "Zed", "guild", "smiths")) == ["forge is lit"]
    say(client, "Mike", "guild", "masons", "no", 409)


def test_a_game_can_leave_a_kind_out(players, monkeypatch):
    client, _ = players
    monkeypatch.setattr(Chat, "kinds", tuple(each for each in Chat.kinds if each.name != "global"))
    say(client, "Mike", "global", "all", "hello", 404)
    assert "global" not in [each["kind"] for each in call(client, "GET", "Mike", "/api/chat/channels")["channels"]]


# --- retention --------------------------------------------------------------------------------------------------------------------

def run_pass(client, days):
    async def go():
        settings = Settings.model_validate({**client.app.state.settings.model_dump(), "chat_retention_days": days})
        return await housekeeping.run(client.app.state.sessionmaker, settings)

    return client.portal.call(go)


def age(client, message_id, days):
    async def work(db):
        then = wallclock.now() - timedelta(days=days)
        await db.execute(update(ChatMessage).where(ChatMessage.id == message_id).values(created_at=then))
        await db.execute(update(ChatFlag).where(ChatFlag.message_id == message_id).values(updated_at=then))

    in_app_db(client, work)


def kept(client):
    async def work(db):
        return sorted((await db.scalars(select(ChatMessage.body))).all())

    return in_app_db(client, work)


def test_old_messages_go_and_a_message_with_an_open_flag_stays(players):
    client, _ = players
    old = say(client, "Zed", "global", "all", "old")["id"]
    flagged = say(client, "Zed", "global", "all", "old but flagged")["id"]
    say(client, "Zed", "global", "all", "new")
    flag(client, "Mike", flagged)
    age(client, old, 10)
    age(client, flagged, 10)
    assert run_pass(client, 7)["old_chat_messages"] == 1
    assert kept(client) == ["new", "old but flagged"]


def test_a_settled_flag_and_its_old_message_go_together(players):
    client, _ = players
    bad = say(client, "Zed", "global", "all", "bad")["id"]
    flag_id = flag(client, "Mike", bad)["id"]
    make_admin(client, "Yan")
    call(client, "POST", "Yan", "/api/chat/admin/settle", {"flag_id": flag_id, "state": "dismissed"})
    age(client, bad, 10)
    run_pass(client, 7)
    assert kept(client) == []


def test_zero_days_keeps_everything(players):
    client, _ = players
    old = say(client, "Zed", "global", "all", "ancient")["id"]
    age(client, old, 3650)
    assert run_pass(client, 0)["old_chat_messages"] == 0
    assert kept(client) == ["ancient"]
