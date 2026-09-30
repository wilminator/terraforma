"""Every engine model, imported here so their tables register together."""

import secrets

from sqlalchemy import BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from .db.base import Base, Timestamps
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
    """A player's login. Their heroes, teams and handle come in the accounts phase."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kept as typed; unique ignoring case (see accounts.service).
    username: Mapped[str] = mapped_column(String(32))
    username_key: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))


class Fighter(Located, Timestamps, Base):
    """Anyone who fights: a hero or a monster. A stand-in for the proof of concept: a name and a place."""

    __tablename__ = "fighters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(64))
