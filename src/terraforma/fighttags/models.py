"""Which fights teams and alliances hold, and how each team did in a finished fight.

Team and alliance ids are plain numbers without links, like a relationship's: they go with the team or alliance."""

from sqlalchemy import Boolean, Double, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: Scores are doubles on every database (a plain ``Float`` is a 4-byte number on MySQL).
SCORE = Double()


class FightTeam(Timestamps, Base):
    """One player's team in a finished fight: whether it won and what it scored (zero unless it did). Written when the fight is scored."""

    __tablename__ = "fight_teams"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False, index=True)
    party: Mapped[int] = mapped_column(Integer)
    pvp: Mapped[bool] = mapped_column(Boolean)
    won: Mapped[bool] = mapped_column(Boolean)
    overkill: Mapped[float] = mapped_column(SCORE, default=0.0)
    underdog: Mapped[float] = mapped_column(SCORE, default=0.0)


class TeamTag(Base):
    """The best fight of a team for one kind (``scoring.KINDS``)."""

    __tablename__ = "team_tags"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), index=True)
    score: Mapped[float] = mapped_column(SCORE)


class TeamKept(Timestamps, Base):
    """A fight a team keeps by hand, in one of its retention slots (``Rules.team_retention_slots``)."""

    __tablename__ = "team_kept"

    team_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False, index=True)


class AllianceTop(Base):
    """A fight in an alliance's top list for a kind (``Rules.alliance_top_size`` of each), put there by $team_id's score."""

    __tablename__ = "alliance_tops"

    alliance_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False, index=True)
    team_id: Mapped[int] = mapped_column(Integer)
    score: Mapped[float] = mapped_column(SCORE)


class AllianceKept(Timestamps, Base):
    """A fight an alliance keeps by hand (``Rules.alliance_retention_slots``), put there by $team_id. It stays after that
    team leaves the alliance, until it is taken out."""

    __tablename__ = "alliance_kept"

    alliance_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False, index=True)
    team_id: Mapped[int] = mapped_column(Integer)
