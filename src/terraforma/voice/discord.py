"""Discord as the voice provider: a bot makes a voice channel in the game's server and an invite to it.

Only Discord's REST API is used, over the standard library, so the engine gains no dependency. The bot needs the *Manage Channels* and *Create
Instant Invite* permissions in the server (``discord_guild_id``). The rooms are voice channels the whole server can see, and an invite lets
anyone who holds it join the server: the link is single-use and lasts five minutes, but the room itself is not private (see README, "Voice").
"""

import asyncio
import json
import urllib.error
import urllib.request

from .hooks import Invite

API = "https://discord.com/api/v10"
VOICE_CHANNEL = 2
INVITE_SECONDS = 300


class VoiceError(Exception):
    """The provider refused or could not be reached; the message is for the player."""


def send(request: urllib.request.Request) -> tuple[int, bytes]:
    """The one place a request leaves the process (blocking; run in a thread). A test replaces it."""
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 (a fixed https address)
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise VoiceError("the voice service can't be reached right now") from error


class DiscordProvider:
    def __init__(self, token: str, guild_id: str, sender=None) -> None:
        self._token = token
        self._guild = guild_id
        self._send = sender or send

    async def _call(self, method: str, path: str, body: dict | None = None) -> dict:
        request = urllib.request.Request(
            f"{API}{path}", method=method, data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bot {self._token}", "Content-Type": "application/json", "User-Agent": "TerraForma (https://github.com/wilminator/terraforma, 0.1)"},
        )
        status, content = await asyncio.to_thread(self._send, request)
        if status == 404 and method == "DELETE":
            return {}
        if status >= 400:
            raise VoiceError("the voice service refused the request" + (" (it is rate limiting; try again in a moment)" if status == 429 else ""))
        return json.loads(content) if content else {}

    async def open_room(self, name: str) -> str:
        return (await self._call("POST", f"/guilds/{self._guild}/channels", {"name": name[:100], "type": VOICE_CHANNEL}))["id"]

    async def invite(self, room_id: str) -> Invite:
        made = await self._call("POST", f"/channels/{room_id}/invites", {"max_age": INVITE_SECONDS, "max_uses": 1, "unique": True})
        return Invite(f"https://discord.gg/{made['code']}", INVITE_SECONDS)

    async def close_room(self, room_id: str) -> None:
        await self._call("DELETE", f"/channels/{room_id}")


def provider_for(settings) -> DiscordProvider | None:
    """The provider the settings name, or None when voice is off."""
    if settings.discord_bot_token and settings.discord_guild_id:
        return DiscordProvider(settings.discord_bot_token, settings.discord_guild_id)
    return None
