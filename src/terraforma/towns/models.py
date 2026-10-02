"""A party in a town: how it was put together, which of its teams are waiting to leave, and who has been told."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class TownVisit(Timestamps, Base):
    """A party that is suspended in a town. ``formation`` is its teams (ids) in the order they stood when it came apart."""

    __tablename__ = "town_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("parties.id"), unique=True)
    formation: Mapped[list] = mapped_column(JSON)


class TownTeam(Base):
    """A team of a suspended party. Teams with the same ``group`` wait and come back together."""

    __tablename__ = "town_teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    visit_id: Mapped[int] = mapped_column(ForeignKey("town_visits.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True)
    group: Mapped[int] = mapped_column(Integer)
    waiting: Mapped[bool] = mapped_column(Boolean, default=False)


class TownNotice(Timestamps, Base):
    """$team_id has been told that $about_team_id is ready to leave (and waiting for it)."""

    __tablename__ = "town_notices"
    __table_args__ = (UniqueConstraint("team_id", "about_team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    about_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"))
