"""A hero belongs to one account, has a job, and stands on a map. A team groups some of an account's heroes."""

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located


class Hero(Located, Timestamps, Base):
    __tablename__ = "heroes"
    __table_args__ = (UniqueConstraint("account_id", "name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # Kept as typed; unique per account ignoring case and spacing (see heroes.service).
    name: Mapped[str] = mapped_column(String(24))
    name_key: Mapped[str] = mapped_column(String(24))
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    level: Mapped[int] = mapped_column(Integer, default=1)
    xp: Mapped[int] = mapped_column(BigInteger, default=0)
    stats: Mapped[dict] = mapped_column(JSON)
    gold: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")


class Team(Timestamps, Base):
    __tablename__ = "teams"
    __table_args__ = (UniqueConstraint("account_id", "name_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    name: Mapped[str] = mapped_column(String(24))
    name_key: Mapped[str] = mapped_column(String(24))


class TeamMember(Base):
    """A hero's place on a team. A hero is on at most one team."""

    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("team_id", "slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), unique=True)
    slot: Mapped[int] = mapped_column(Integer)


class HeroItem(Base):
    """A stack of one kind of item in a hero's inventory, at a position (0, 1, 2, ... with no gaps; heroes.inventory keeps it so)."""

    __tablename__ = "hero_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    position: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer, default=1)


class HeroEquipment(Base):
    """What a hero wears or wields in one slot. A two-handed weapon holds both hand slots, with the same stack in each."""

    __tablename__ = "hero_equipment"
    __table_args__ = (UniqueConstraint("hero_id", "slot"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), index=True)
    slot: Mapped[str] = mapped_column(String(32))
    hero_item_id: Mapped[int] = mapped_column(ForeignKey("hero_items.id"), index=True)


class HeroAbility(Base):
    """An ability a hero has learned."""

    __tablename__ = "hero_abilities"
    __table_args__ = (UniqueConstraint("hero_id", "ability_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), index=True)
    ability_id: Mapped[int] = mapped_column(ForeignKey("abilities.id"))
