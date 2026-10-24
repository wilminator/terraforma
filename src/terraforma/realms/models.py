"""Realms (kingdoms) and the settlements that belong to them: the living world's towns, villages and ruins."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
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
    the game's ``Realms`` hook moves it over time. ``key`` is the game's own name for the realm, stable across releases."""

    __tablename__ = "realms"
    __table_args__ = (UniqueConstraint("world_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    strength: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: The world-clock tick the strength was last brought up to (see ``realms.strength``).
    as_of_tick: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


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


class Material(Timestamps, Base):
    """A raw material the world's crafters work from (iron, leather, a rare ore). The game picks the ``key``."""

    __tablename__ = "materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class SettlementSupply(Timestamps, Base):
    """How much of a material a settlement has, and what moves it. Time is the world clock (``World.tick``), and the level is only brought
    up to date when it is read or changed (``realms.supply.settle``): from ``as_of_tick`` on, every tick adds ``rate`` (the settlement's own
    sources) cut by ``cut`` percent (an event: a raid, a closed road), and takes away ``drain`` (what its crafters and people use up).
    The level stays between 0 and ``capacity``."""

    __tablename__ = "settlement_supplies"
    __table_args__ = (UniqueConstraint("settlement_id", "material_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("settlements.id"), index=True)
    material_id: Mapped[int] = mapped_column(ForeignKey("materials.id"), index=True)
    level: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    capacity: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    rate: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    drain: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    cut: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    as_of_tick: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
