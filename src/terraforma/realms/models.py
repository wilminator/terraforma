"""Realms (kingdoms) and the settlements that belong to them: the living world's towns, villages and ruins."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: A settlement a party can come to.
LIVE = "live"
#: A settlement that fell: closed and hidden, kept so it can be founded again. It is on no team's visited list.
RUINS = "ruins"
#: A settlement authored in advance and not yet founded: it wakes when its realm grows (or an admin says so).
DORMANT = "dormant"
STATES = (LIVE, RUINS, DORMANT)


class Realm(Timestamps, Base):
    """A realm (a kingdom or country) of a world. ``strength`` is a whole number the rules move (play, time, an admin's nudge);
    the engine only keeps it. ``key`` is the game's own name for the realm, stable across releases."""

    __tablename__ = "realms"
    __table_args__ = (UniqueConstraint("world_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    strength: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class Settlement(Timestamps, Base):
    """A town, city or village of a realm, on a map. ``state`` is live, ruins or dormant (see above). ``tier`` is how big it is
    (0 is a hamlet; a game says what the rest mean). More than one settlement may share an authored map (a limit on that is
    a rule of founding, not of this table). ``key`` is the game's own name for it, stable across releases."""

    __tablename__ = "settlements"
    __table_args__ = (UniqueConstraint("world_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    realm_id: Mapped[int] = mapped_column(ForeignKey("realms.id"), index=True)
    map_id: Mapped[int] = mapped_column(ForeignKey("maps.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), default=LIVE, index=True)
    tier: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
