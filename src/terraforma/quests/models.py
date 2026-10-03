"""A team's quest record, and its markers for quests in progress. Quests belong to the team, not the player or the hero."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class QuestRecord(Timestamps, Base):
    """How many quests of a category and level the team has completed: permanent."""

    __tablename__ = "quest_records"
    __table_args__ = (UniqueConstraint("team_id", "category", "level"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    category: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer)
    count: Mapped[int] = mapped_column(Integer, default=0)


class QuestMarker(Timestamps, Base):
    """Where a team is in a quest that is not finished: a whole number the quest's dialog sets and reads. No row means 0."""

    __tablename__ = "quest_markers"
    __table_args__ = (UniqueConstraint("team_id", "quest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    quest: Mapped[str] = mapped_column(String(64))
    value: Mapped[int] = mapped_column(Integer)
