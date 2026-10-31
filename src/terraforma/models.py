"""Every engine model, imported here so their tables register together."""

import secrets
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, false, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .db.base import Base, Timestamps
from .keys import encrypted_column
from .world.location import Located, Position


def new_seed() -> int:
    # Fits a signed 64-bit column on every database.
    return secrets.randbits(63)


class World(Timestamps, Base):
    """A world: its seed (all its randomness) and its clock.

    The clock counts ticks, the engine's own time, not the wall clock:
    the world's generators (economy, politics, quests, the maps changing)
    run as it advances, and tests can fast-forward it.
    """

    __tablename__ = "worlds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    seed: Mapped[int] = mapped_column(BigInteger, default=new_seed)
    tick: Mapped[int] = mapped_column(BigInteger, default=0)


async def advance_clock(session: AsyncSession, world_id: int, ticks: int = 1) -> int:
    """Moves the world's clock on by $ticks, safely alongside other writers. Returns the new tick."""
    if ticks < 1:
        raise ValueError("the clock only moves forward")
    await session.execute(update(World).where(World.id == world_id).values(tick=World.tick + ticks))
    world = await session.get(World, world_id, populate_existing=True)
    return world.tick


class Map(Timestamps, Base):
    """A map in a world: a grid of tiles and a grid of zones over it. The hub is one too.

    ``tileset`` lists the kinds of tile (``content.schema.Tile``: passable, poison, encounter rate, art) and ``tiles``
    is the grid, rows top to bottom, of places in it. ``zones`` lists the kinds of area (``content.schema.Zone``: its
    encounter table, area drops, PvP flag) and ``zone_tiles`` is their grid. A map with no grids (the default hub) is
    open ground in a single plain zone. ``revision`` goes up whenever the map's content changes, so a client can notice
    that the map it holds is old. The grids are whole numbers, stored as plain JSON.
    """

    __tablename__ = "maps"
    __table_args__ = (UniqueConstraint("world_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    # The slug that locations and the browser use (see world.location.Position); ``title`` is what players read.
    name: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(64), default="", server_default="")
    width: Mapped[int] = mapped_column(Integer, default=1)
    height: Mapped[int] = mapped_column(Integer, default=1)
    # Walking off the left or right edge comes back on the other side; likewise top and bottom. A wrapped edge is no edge.
    wrap_x: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    wrap_y: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Steps a party takes after entering the map, or after a fight, before it can meet monsters.
    safe_steps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    tileset: Mapped[list | None] = mapped_column(JSON)
    tiles: Mapped[list | None] = mapped_column(JSON)
    zones: Mapped[list | None] = mapped_column(JSON)
    zone_tiles: Mapped[list | None] = mapped_column(JSON)

    def contains(self, position: Position) -> bool:
        return position.map == self.name and 0 <= position.x < self.width and 0 <= position.y < self.height

    def normalize(self, x: int, y: int) -> tuple[int, int] | None:
        """The tile (x, y) means on this map: wrapped round an edge that wraps, None if it is off the map."""
        if self.wrap_x:
            x %= self.width
        if self.wrap_y:
            y %= self.height
        return (x, y) if 0 <= x < self.width and 0 <= y < self.height else None

    def tile(self, x: int, y: int) -> dict:
        """What kind of tile is at (x, y) (wrapped as ``normalize`` does). Off the map, or on a map with no grid, it is open ground."""
        spot = self.normalize(x, y)
        if spot is None or not self.tiles:
            return dict(OPEN_GROUND)
        return {**OPEN_GROUND, **self.tileset[self.tiles[spot[1]][spot[0]]]}

    def zone(self, x: int, y: int) -> dict:
        """What kind of area is at (x, y): its encounter table, area drops and PvP flag. A plain zone off the grid."""
        spot = self.normalize(x, y)
        if spot is None or not self.zones:
            return dict(PLAIN_ZONE)
        index = self.zone_tiles[spot[1]][spot[0]] if self.zone_tiles else 0
        return {**PLAIN_ZONE, **self.zones[index]}


OPEN_GROUND = {"name": "", "passable": True, "poison": False, "encounter_rate": 0, "art": None}
PLAIN_ZONE = {"name": "", "encounters": [], "drops": [], "pvp": False, "can_flee": True}


class Account(Timestamps, Base):
    """A player's login and public handle. Their heroes and teams come later."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kept as typed; unique ignoring case (see accounts.service).
    username: Mapped[str] = mapped_column(String(32))
    username_key: Mapped[str] = mapped_column(String(32), unique=True)
    # NULL for an account made by signing in with an external provider that has no local password (yet).
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # Required for new accounts; NULL only for rows made before email existed.
    email: Mapped[str | None] = mapped_column(String(254))
    email_key: Mapped[str | None] = mapped_column(String(254), unique=True)
    # No login until the address is confirmed by the emailed link.
    email_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The name other players see. Never the same as the username or email
    # (see accounts.service.check_handle); unique ignoring case and spacing.
    handle: Mapped[str | None] = mapped_column(String(24))
    handle_key: Mapped[str | None] = mapped_column(String(24), unique=True)
    # Every login carries this number; bumping it ends them all (a password reset does).
    session_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Two-factor login (see accounts.twofa). The secret is encrypted; it is set
    # when setup starts and only counts once totp_enabled_at is.
    totp_secret: Mapped[str | None] = mapped_column(String(255))
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The last 30-second step a code was accepted for: each code works once.
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)
    # SHA-256 of each unused recovery code.
    recovery_codes: Mapped[list | None] = mapped_column(JSON)
    # Names the one emailed 2FA change that is still open (a newer request replaces it).
    twofa_change_nonce: Mapped[str | None] = mapped_column(String(43))
    # How many security keys (WebAuthn) count as a second factor; kept in step with the confirmed rows of webauthn_keys, so
    # "is two-factor login on" never needs a query.
    webauthn_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set only by a game's own setup (``challenge.service.set_admin``), never by a call a player can make.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


encrypted_column(Account.__table__.c.totp_secret, "accounts.totp_secret")


class ExternalIdentity(Timestamps, Base):
    """An account at an external sign-in provider (see accounts.external_routes) that signs in to one local account."""

    __tablename__ = "external_identities"
    __table_args__ = (UniqueConstraint("provider", "subject"), UniqueConstraint("account_id", "provider"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    # The provider's name in settings.toml.
    provider: Mapped[str] = mapped_column(String(32))
    # The provider's stable id for the person (the "sub" claim); the email may change, this does not.
    subject: Mapped[str] = mapped_column(String(255))
    # The address the provider gave when this was linked, for showing which account it is.
    email: Mapped[str | None] = mapped_column(String(254))


class WebauthnKey(Timestamps, Base):
    """A security key or passkey (WebAuthn) of an account: a second factor next to the authenticator app (see accounts.keys).

    A key counts once ``confirmed_at`` is set, which the emailed link does; a registration that was never confirmed is
    replaced by the next one and tidied up by housekeeping.
    """

    __tablename__ = "webauthn_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # The authenticator's id for the key, base64url (up to 1023 bytes by the standard; real ones are 16 to 128).
    credential_id: Mapped[str] = mapped_column(String(255), unique=True)
    # The COSE public key, as the authenticator gave it.
    # The COSE public key as base64url text (a bytes parameter does not go through every driver).
    public_key: Mapped[str] = mapped_column(String(1024))
    # The authenticator's counter at its last use: a counter that does not go up is a cloned key (0 means it keeps none).
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    # What the player calls it ("Phone", "YubiKey").
    name: Mapped[str] = mapped_column(String(40))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RememberedBrowser(Timestamps, Base):
    """A browser that proved a second factor and may skip it until ``expires_at`` (see accounts.remember).

    The browser holds a random secret in a cookie; only its hash is kept here. It stops counting when the account's session
    version moves on (a password reset, an email change) and when two-factor login changes.
    """

    __tablename__ = "remembered_browsers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    session_version: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # The browser's own words for itself, cut short, so the player can tell which is which.
    label: Mapped[str] = mapped_column(String(80), default="")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitHit(Base):
    """Attempts counted for one rate limit, one subject (hashed), one window of time."""

    __tablename__ = "rate_limit_hits"

    bucket: Mapped[str] = mapped_column(String(32), primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_start: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    hits: Mapped[int] = mapped_column(Integer, default=0)


class Fighter(Located, Timestamps, Base):
    """Anyone who fights: a hero or a monster. A stand-in for the proof of concept: a name and a place."""

    __tablename__ = "fighters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))

# The content and hero tables register with the rest.
from .content import models as _content  # noqa: E402,F401
from .heroes import models as _heroes  # noqa: E402,F401
from .fights import models as _fights  # noqa: E402,F401
from .parties import models as _parties  # noqa: E402,F401
from .standing import models as _standing  # noqa: E402,F401
from .alliances import models as _alliances  # noqa: E402,F401
from .relations import models as _relations  # noqa: E402,F401
from .towns import models as _towns  # noqa: E402,F401
from .trading import models as _trading  # noqa: E402,F401
from .storage import models as _storage  # noqa: E402,F401
from .delivery import models as _delivery  # noqa: E402,F401
from .escrow import models as _escrow  # noqa: E402,F401
from .marketplace import models as _marketplace  # noqa: E402,F401
from .profiles import models as _profiles  # noqa: E402,F401
from .coliseum import models as _coliseum  # noqa: E402,F401
from .contracts import models as _contracts  # noqa: E402,F401
from .challenge import models as _challenge  # noqa: E402,F401
from .npcs import models as _npcs  # noqa: E402,F401
from .disuse import models as _disuse  # noqa: E402,F401
from .maps import models as _maps  # noqa: E402,F401
from .quests import models as _quests  # noqa: E402,F401
from .instances import models as _instances  # noqa: E402,F401
from .quests import weekly_models as _weekly_quests  # noqa: E402,F401
from .quests import rotation_models as _rotation_quests  # noqa: E402,F401
from .quests import needs_models as _needs_quests  # noqa: E402,F401
from .quests import boss_models as _boss_quests  # noqa: E402,F401
from .npcs import fight_models as _scripted_fights  # noqa: E402,F401
from .quests import manual_models as _manual_quests  # noqa: E402,F401
from .chat import models as _chat  # noqa: E402,F401
from .guild import models as _guild  # noqa: E402,F401
from .playtime import models as _playtime  # noqa: E402,F401
from .social import models as _social  # noqa: E402,F401
from .monsterplay import models as _monsterplay  # noqa: E402,F401
from .storegrant import models as _storegrant  # noqa: E402,F401
from .fighttags import models as _fighttags  # noqa: E402,F401
from .fightpages import models as _fightpages  # noqa: E402,F401
from .voice import models as _voice  # noqa: E402,F401
from .oidc import models as _oidc  # noqa: E402,F401
from .gamestate import models as _gamestate  # noqa: E402,F401
from .realms import models as _realms  # noqa: E402,F401
