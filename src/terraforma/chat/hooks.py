"""What a game decides about chat: ``Chat``, the class a game overrides (``Game(chat=...)``), and the kinds of channel.

A *channel* is a kind and a key: ``("party", "12")`` is the chat of party 12. Every kind answers the same few questions, so the
engine's calls treat a party, an alliance, the people nearby, a town, everyone and a whisper alike, and a game adds a kind (a
guild, a dungeon run) by writing one more ``ChannelKind`` and listing it: no call changes.

    class Guildhall(ChannelKind):
        name = "guild"
        async def member(self, session, game, account_id, key):
            return await in_guild(session, account_id, int(key))

    class Courteous(Chat):
        kinds = (*Chat.kinds, Guildhall())
        async def may_speak(self, session, speaker, kind, key, body):
            return "mind your manners" if "rude" in body else None

This is a public interface (the license exception covers it): the names and signatures are what games build on.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alliances.models import Alliance, AllianceMember
from ..heroes.models import Hero, Team
from ..models import Account, Map
from ..parties.models import Party, PartyTeam
from ..reach.hooks import distance
from ..accounts.service import handle_key
from .models import BODY_MAX, ChatMessage

if TYPE_CHECKING:
    from ..game import Game


@dataclass(frozen=True)
class Channel:
    """A channel a player can use: its kind, its key and a name to show."""

    kind: str
    key: str
    label: str


@dataclass(frozen=True)
class Place:
    """Where a speaker stood: a map and a tile."""

    map_id: int
    x: int
    y: int


async def place_of(session: AsyncSession, account_id: int) -> Place | None:
    """Where the player is: their party's tile when a team of theirs is in a party, else their first hero's, else nowhere."""
    row = (await session.execute(
        select(Party.map_id, Party.x, Party.y)
        .join(PartyTeam, PartyTeam.party_id == Party.id)
        .join(Team, Team.id == PartyTeam.team_id)
        .where(Team.account_id == account_id)
        .order_by(Party.id)
        .limit(1)
    )).first()
    if row is None:
        row = (await session.execute(select(Hero.map_id, Hero.x, Hero.y).where(Hero.account_id == account_id).order_by(Hero.id).limit(1))).first()
    return Place(*row) if row is not None else None


class ChannelKind:
    """One kind of channel. Override ``member`` (who may read and speak), and as needed the rest; the defaults suit a kind
    whose key is used as it stands and which everyone in it hears everything of."""

    name: str = ""

    async def resolve(self, session: AsyncSession, game: "Game", account_id: int, key: str) -> str | None:
        """The key the channel is stored under, for a key as the player gave it; None when there is no such channel. A whisper turns
        the other player's handle into the pair of players, which is the same channel from both sides."""
        return key

    async def member(self, session: AsyncSession, game: "Game", account_id: int, key: str) -> bool:
        """Whether the player may read and speak in the channel (a key already resolved)."""
        return False

    async def channels(self, session: AsyncSession, game: "Game", account_id: int) -> list[Channel]:
        """The channels of this kind the player is in now, for the channel list."""
        return []

    async def hears(self, session: AsyncSession, game: "Game", reader_id: int, spoke: Place | None) -> bool:
        """Whether a member standing where they do now hears something said from $spoke. GlobalChat does, by default."""
        return True


class PartyChat(ChannelKind):
    """The party's chat, for the players who have a team in it. Key: the party's id."""

    name = "party"

    async def member(self, session, game, account_id, key):
        if not key.isdigit():
            return False
        return await session.scalar(select(PartyTeam.id).join(Team, Team.id == PartyTeam.team_id).where(PartyTeam.party_id == int(key), Team.account_id == account_id).limit(1)) is not None

    async def channels(self, session, game, account_id):
        ids = (await session.scalars(select(PartyTeam.party_id).join(Team, Team.id == PartyTeam.team_id).where(Team.account_id == account_id).distinct().order_by(PartyTeam.party_id))).all()
        return [Channel(self.name, str(each), "Party") for each in ids]


class AllianceChat(ChannelKind):
    """The alliance's chat, for the players who have a team in it. Key: the alliance's id."""

    name = "alliance"

    async def member(self, session, game, account_id, key):
        if not key.isdigit():
            return False
        return await session.scalar(select(AllianceMember.id).join(Team, Team.id == AllianceMember.team_id).where(AllianceMember.alliance_id == int(key), Team.account_id == account_id).limit(1)) is not None

    async def channels(self, session, game, account_id):
        rows = (await session.execute(select(Alliance.id, Alliance.name).join(AllianceMember, AllianceMember.alliance_id == Alliance.id).join(Team, Team.id == AllianceMember.team_id).where(Team.account_id == account_id).distinct().order_by(Alliance.id))).all()
        return [Channel(self.name, str(each), name) for each, name in rows]


class NearbyChat(ChannelKind):
    """What is said close by, on the map the player stands on. Key: the map's id. A member hears it from ``range`` tiles."""

    name = "nearby"

    def __init__(self, range: int = 10) -> None:
        self.range = range

    async def member(self, session, game, account_id, key):
        place = await place_of(session, account_id)
        return place is not None and key == str(place.map_id)

    async def channels(self, session, game, account_id):
        place = await place_of(session, account_id)
        return [Channel(self.name, str(place.map_id), "NearbyChat")] if place else []

    async def hears(self, session, game, reader_id, spoke):
        here = await place_of(session, reader_id)
        if here is None or spoke is None or here.map_id != spoke.map_id:
            return False
        return distance(await session.get(Map, here.map_id), (here.x, here.y), (spoke.x, spoke.y)) <= self.range


class TownChat(ChannelKind):
    """The chat of a town (the game's ``Towns.is_town`` says which places are), for whoever stands in it. Key: the town map's id."""

    name = "town"

    async def member(self, session, game, account_id, key):
        place = await place_of(session, account_id)
        return place is not None and key == str(place.map_id) and await game.towns.is_town(session, place.map_id, place.x, place.y)

    async def channels(self, session, game, account_id):
        place = await place_of(session, account_id)
        if place is not None and await game.towns.is_town(session, place.map_id, place.x, place.y):
            return [Channel(self.name, str(place.map_id), "TownChat")]
        return []


class GlobalChat(ChannelKind):
    """One channel for every player. Key: ``all``."""

    name = "global"

    async def member(self, session, game, account_id, key):
        return key == "all"

    async def channels(self, session, game, account_id):
        return [Channel(self.name, "all", "GlobalChat")]


class WhisperChat(ChannelKind):
    """Two players talking. The player gives the other's handle (or the pair, as the channel list shows it); it is stored as the pair of ids, so it is one channel from both sides."""

    name = "whisper"

    async def resolve(self, session, game, account_id, key):
        if ":" in key:  # the pair itself, as the channel list gives it (a handle never holds a colon)
            return key if str(account_id) in key.split(":") else None
        other = await session.scalar(select(Account.id).where(Account.handle_key == handle_key(key)))
        if other is None or other == account_id:
            return None
        return f"{min(account_id, other)}:{max(account_id, other)}"

    async def member(self, session, game, account_id, key):
        return str(account_id) in key.split(":")

    async def channels(self, session, game, account_id):
        mine = str(account_id)
        keys = (await session.scalars(select(ChatMessage.key).where(ChatMessage.kind == self.name, or_(ChatMessage.key.like(f"{mine}:%"), ChatMessage.key.like(f"%:{mine}"))).distinct())).all()
        out = []
        for key in keys:
            other = next(int(each) for each in key.split(":") if each != mine)
            out.append(Channel(self.name, key, await session.scalar(select(Account.handle).where(Account.id == other)) or "?"))
        return out


class Chat:
    #: The kinds of channel, by their ``name``. A game takes some out, or adds its own.
    kinds: tuple[ChannelKind, ...] = (PartyChat(), AllianceChat(), NearbyChat(), TownChat(), GlobalChat(), WhisperChat())
    #: The longest message, in characters.
    body_max: int = BODY_MAX
    #: How many messages a first read of a channel brings.
    history: int = 50
    #: The reasons a player may give when flagging a message.
    flag_reasons: tuple[str, ...] = ("tou", "spam", "harassment", "other")

    def kind(self, name: str) -> ChannelKind | None:
        return next((each for each in self.kinds if each.name == name), None)

    def clean(self, body: str) -> str:
        """The text as it is kept: trimmed, with runs of white space made single spaces. A game may filter words here."""
        return " ".join(body.split())

    async def may_speak(self, session: AsyncSession, speaker: Account, kind: str, key: str, body: str) -> str | None:
        """Why the player may not say this just now (a muted player, a rule against links...), or None. Anyone may by default."""
        return None
