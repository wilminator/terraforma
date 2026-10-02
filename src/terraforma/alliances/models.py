"""Alliances of teams: who is in one and in what role, and who has been asked."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps, utcnow

NAME_MAX = 32


class Alliance(Timestamps, Base):
    __tablename__ = "alliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kept as typed; unique ignoring case and spacing.
    name: Mapped[str] = mapped_column(String(NAME_MAX))
    name_key: Mapped[str] = mapped_column(String(NAME_MAX), unique=True)


class AllianceMember(Base):
    """A team in an alliance, in a role (one of the game's ``Alliances.roles``). ``id`` runs in the order teams joined."""

    __tablename__ = "alliance_members"
    __table_args__ = (UniqueConstraint("alliance_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AllianceInvite(Base):
    """An alliance's invitation to a team, until the team accepts or declines it or the alliance withdraws it."""

    __tablename__ = "alliance_invites"
    __table_args__ = (UniqueConstraint("alliance_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alliance_id: Mapped[int] = mapped_column(ForeignKey("alliances.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    invited_by_team_id: Mapped[int] = mapped_column(Integer)
