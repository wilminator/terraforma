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
