"""Voice: rooms for chat channels at a provider (Discord), joining them, the provider's calls, and housekeeping closing old rooms."""

import json
import urllib.request
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from terraforma import housekeeping, wallclock
from terraforma.chat.hooks import Chat
from terraforma.voice import discord
from terraforma.voice.discord import DiscordProvider, VoiceError
from terraforma.voice.models import VoiceRoom
from terraforma.voice.hooks import Voice
from terraforma.testing import in_app_db

from .test_chat import call, game, one_party, players  # noqa: F401  (the fixtures and helpers chat's tests use)

pytestmark = pytest.mark.social

TOKEN = "x" * 59 + ".y" * 3


class FakeDiscord:
    """Stands in for Discord's server: remembers what it was asked and answers like it."""

    def __init__(self):
        self.requests = []
        self.fail = None
        self.channels = 0

    def __call__(self, request: urllib.request.Request):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url.removeprefix(discord.API), body, request.get_header("Authorization")))
        if self.fail:
            return self.fail, b"{}"
        if request.full_url.endswith("/channels") and request.get_method() == "POST":
            self.channels += 1
            return 201, json.dumps({"id": f"90{self.channels}"}).encode()
        if request.full_url.endswith("/invites"):
            return 200, json.dumps({"code": f"code{len(self.requests)}"}).encode()
        return 204, b""

    def paths(self):
        return [(method, path) for method, path, _, _ in self.requests]


@pytest.fixture
def fake(monkeypatch):
    fake = FakeDiscord()
    monkeypatch.setattr(discord, "send", fake)
    return fake


@pytest.fixture
def voiced(players):  # noqa: F811
    """The chat players, with voice turned on in the settings."""
    client, ids = players
    client.app.state.settings = client.app.state.settings.model_copy(update={"discord_bot_token": TOKEN, "discord_guild_id": "123456789"})
    return client, ids


def join(client, who, kind, key, status=200):
    return call(client, "POST", who, "/api/voice/join", {"kind": kind, "key": key}, status)


# --- on and off ----------------------------------------------------------------------------------------------------------------

def test_voice_is_off_without_the_discord_settings(players):
    client, ids = players
    assert call(client, "GET", "Mike", "/api/voice") == {"enabled": False, "kinds": []}
    assert "not turned on" in join(client, "Mike", "alliance", str(ids["alliance"]), 404)["detail"]


def test_voice_is_on_with_both_settings_and_says_which_channels_have_rooms(voiced):
    client, _ = voiced
    assert call(client, "GET", "Mike", "/api/voice") == {"enabled": True, "kinds": ["alliance", "party"]}


def test_one_setting_alone_leaves_voice_off(players):
    client, _ = players
    client.app.state.settings = client.app.state.settings.model_copy(update={"discord_bot_token": TOKEN})
    assert call(client, "GET", "Mike", "/api/voice")["enabled"] is False


def test_the_guild_id_is_digits_only():
    from terraforma.settings import Settings

    with pytest.raises(ValueError):
        Settings(session_secret="x" * 32, discord_guild_id="not a number")


# --- joining -------------------------------------------------------------------------------------------------------------------

def test_a_member_gets_a_single_use_link_and_the_room_is_made_once(voiced, fake):
    client, ids = voiced
    party = str(one_party(client, ids))
    first = join(client, "Mike", "party", party)
    assert first["url"].startswith("https://discord.gg/code") and first["seconds"] == 300
    second = join(client, "Zed", "party", party)
    assert second["url"] != first["url"], "every member gets a link of their own"
    assert fake.paths() == [("POST", "/guilds/123456789/channels"), ("POST", "/channels/901/invites"), ("POST", "/channels/901/invites")]
    method, path, body, auth = fake.requests[0]
    assert body == {"name": f"party-{party}", "type": 2} and auth == f"Bot {TOKEN}"
    assert fake.requests[1][2] == {"max_age": 300, "max_uses": 1, "unique": True}

    async def rooms(db):
        return await db.scalar(select(func.count()).select_from(VoiceRoom))

    assert in_app_db(client, rooms) == 1


def test_only_members_of_the_channel_get_a_link(voiced, fake):
    client, ids = voiced
    party = str(one_party(client, ids))
    assert "not in that channel" in join(client, "Yan", "party", party, 409)["detail"]
    join(client, "Yan", "alliance", str(ids["alliance"]), 409)
    join(client, "Yan", "party", "nonsense", 409)
    assert fake.requests == [], "nothing is asked of Discord for someone who may not join"


def test_a_channel_of_a_kind_without_rooms_has_no_voice(voiced, fake):
    client, _ = voiced
    assert "no voice room" in join(client, "Mike", "global", "all", 409)["detail"]
    join(client, "Mike", "whisper", "Zedthe" + "Bold", 409)
    assert fake.requests == []


def test_a_game_chooses_which_channels_have_rooms_and_their_names(voiced, fake, monkeypatch):
    client, ids = voiced
    monkeypatch.setattr(Voice, "kinds", frozenset({"alliance"}))
    monkeypatch.setattr(Voice, "room_name", lambda self, kind, key: f"Pact {key}")
    join(client, "Mike", "alliance", str(ids["alliance"]))
    assert fake.requests[0][2]["name"] == f"Pact {ids['alliance']}"
    join(client, "Mike", "party", str(one_party(client, ids)), 409)


def test_when_discord_says_no_the_player_is_told_and_no_room_is_kept(voiced, fake):
    client, ids = voiced
    fake.fail = 403
    answer = join(client, "Mike", "alliance", str(ids["alliance"]), 502)
    assert answer["detail"] == "the voice service refused the request"
    fake.fail = 429
    assert "rate limiting" in join(client, "Mike", "alliance", str(ids["alliance"]), 502)["detail"]

    async def rooms(db):
        return await db.scalar(select(func.count()).select_from(VoiceRoom))

    assert in_app_db(client, rooms) == 0


def test_voice_needs_a_login(app_client):
    app_client.get("/api/voice")
    assert app_client.get("/api/voice").status_code == 401
    assert app_client.post("/api/voice/join", json={"kind": "party", "key": "1"}).status_code == 401


# --- the provider ----------------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_the_discord_provider_speaks_the_rest_api_and_hides_the_token_in_its_errors():
    sent = FakeDiscord()
    provider = DiscordProvider(TOKEN, "42", sender=sent)
    assert await provider.open_room("a" * 150) == "901"
    assert len(sent.requests[0][2]["name"]) == 100
    assert (await provider.invite("901")).url == "https://discord.gg/code2"
    await provider.close_room("901")
    assert sent.paths()[-1] == ("DELETE", "/channels/901")
    sent.fail = 500
    with pytest.raises(VoiceError) as error:
        await provider.invite("901")
    assert TOKEN not in str(error.value)


@pytest.mark.anyio
async def test_closing_a_room_that_is_already_gone_is_fine():
    class Gone(FakeDiscord):
        def __call__(self, request):
            return 404, b"{}"

    await DiscordProvider(TOKEN, "42", sender=Gone()).close_room("901")


@pytest.mark.anyio
async def test_an_unreachable_discord_is_a_voice_error(monkeypatch):
    def refuse(request, timeout):
        raise OSError("no route")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    with pytest.raises(VoiceError):
        await DiscordProvider(TOKEN, "42").open_room("x")


# --- housekeeping ----------------------------------------------------------------------------------------------------------------

def run_pass(client):
    async def go():
        return await housekeeping.run(client.app.state.sessionmaker, client.app.state.settings)

    return client.portal.call(go)


def age_rooms(client, hours, only=None):
    async def work(db):
        query = update(VoiceRoom).values(created_at=wallclock.now() - timedelta(hours=hours))
        if only:
            query = query.where(VoiceRoom.kind == only)
        await db.execute(query)

    in_app_db(client, work)


def room_count(client):
    async def work(db):
        return await db.scalar(select(func.count()).select_from(VoiceRoom))

    return in_app_db(client, work)


def test_old_rooms_are_closed_at_discord_and_forgotten_and_young_ones_stay(voiced, fake):
    client, ids = voiced
    join(client, "Mike", "alliance", str(ids["alliance"]))
    join(client, "Mike", "party", str(one_party(client, ids)))
    age_rooms(client, 25, only="alliance")
    assert run_pass(client)["old_voice_rooms"] == 1
    assert ("DELETE", "/channels/901") in fake.paths() and ("DELETE", "/channels/902") not in fake.paths()
    assert room_count(client) == 1


def test_a_room_discord_will_not_close_is_kept_for_the_next_pass(voiced, fake):
    client, ids = voiced
    join(client, "Mike", "alliance", str(ids["alliance"]))
    age_rooms(client, 25)
    fake.fail = 500
    assert run_pass(client)["old_voice_rooms"] == 0 and room_count(client) == 1
    fake.fail = None
    assert run_pass(client)["old_voice_rooms"] == 1 and room_count(client) == 0


def test_with_voice_off_housekeeping_touches_no_room(players):
    client, ids = players
    async def room(db):
        db.add(VoiceRoom(kind="party", key="1", provider_id="1", created_at=wallclock.now() - timedelta(days=30)))

    in_app_db(client, room)
    assert run_pass(client)["old_voice_rooms"] == 0 and room_count(client) == 1
