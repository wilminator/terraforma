"""Voice: talking by voice in the rooms of a chat channel, through a provider outside the engine.

The engine does not carry audio. It asks a provider (Discord, with a bot) to make a voice room for a channel and gives a member a short-lived
link to join it; the audio stays with the provider. Off unless ``settings.toml`` names the provider's credentials (``discord_bot_token`` and
``discord_guild_id``). A game hands ``Game(voice=...)`` a subclass of ``Voice`` to choose which kinds of channel get rooms and what they are called.

    class Voice(terraforma.voice.hooks.Voice):
        kinds = frozenset({"party"})
        def room_name(self, kind, key):
            return f"Party {key}"

This is a public interface (the license exception covers it): the names and signatures are what games build on.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Invite:
    """A link a member opens to join a room, and how long it lasts, in seconds."""

    url: str
    seconds: int


class VoiceProvider(Protocol):
    """A voice service. ``open_room`` makes a room and returns the provider's name for it; ``invite`` makes a link to a room that
    works once, for a few minutes; ``close_room`` removes it (and does nothing if it is already gone)."""

    async def open_room(self, name: str) -> str: ...

    async def invite(self, room_id: str) -> Invite: ...

    async def close_room(self, room_id: str) -> None: ...


class Voice:
    #: The kinds of chat channel that get a voice room. A channel of another kind has none.
    kinds: frozenset[str] = frozenset({"party", "alliance"})

    def room_name(self, kind: str, key: str) -> str:
        """The room's name at the provider: shown to everyone who can see the provider's room list, so it names no player."""
        return f"{kind}-{key}"
