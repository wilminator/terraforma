"""Which fights a team's rotating quest was fought in, so the quest can be settled by how the fight ended."""

from sqlalchemy import ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class QuestBossFight(Timestamps, Base):
    """A fight that was the boss fight of a team's taken slot quest (``quests.bossfight``). A party fight with several teams on the same
    quest has a row for each team's progress. The rows go with the fight when it is swept (``fighttags.service``)."""

    __tablename__ = "quest_boss_fights"
    __table_args__ = (UniqueConstraint("fight_id", "progress_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), index=True)
    progress_id: Mapped[int] = mapped_column(ForeignKey("team_slot_quests.id"), index=True)
