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
    """A map in a world. The hub is one too, until the overworld arrives."""

    __tablename__ = "maps"
    __table_args__ = (UniqueConstraint("world_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    width: Mapped[int] = mapped_column(Integer, default=1)
    height: Mapped[int] = mapped_column(Integer, default=1)
    # Walking off one edge comes back on the other.
    wraps: Mapped[bool] = mapped_column(Boolean, default=False)

    def contains(self, position: Position) -> bool:
        return position.map == self.name and 0 <= position.x < self.width and 0 <= position.y < self.height


class Account(Timestamps, Base):
    """A player's login and public handle. Their heroes and teams come later."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kept as typed; unique ignoring case (see accounts.service).
    username: Mapped[str] = mapped_column(String(32))
    username_key: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
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
    # Set only by a game's own setup (``challenge.service.set_admin``), never by a call a player can make.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


encrypted_column(Account.__table__.c.totp_secret, "accounts.totp_secret")


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
from .alliances import models as _alliances  # noqa: E402,F401
from .relations import models as _relations  # noqa: E402,F401
from .towns import models as _towns  # noqa: E402,F401
from .trading import models as _trading  # noqa: E402,F401
from .profiles import models as _profiles  # noqa: E402,F401
from .challenge import models as _challenge  # noqa: E402,F401
from .market import models as _market  # noqa: E402,F401
