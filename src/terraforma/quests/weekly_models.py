"""Quest templates, and the weekly quest each team is offered. Every generated weekly quest is kept, so it can become a template."""

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: Where a template came from: the game's seed file, or a generated quest an admin promoted.
SEEDED, PROMOTED = "seed", "promoted"
#: Where a weekly quest came from: a template (scaled to the team), or made up from nothing.
FROM_TEMPLATE, GENERATED = "template", "generated"
#: Where a weekly quest stands: offered (not taken), active (taken: the dungeon is open), completed or failed (one try only).
OFFERED, ACTIVE, COMPLETED, FAILED = "offered", "active", "completed", "failed"


class QuestTemplate(Timestamps, Base):
    """A weekly quest written by hand, or promoted from a generated one. ``spec`` is the game's own description of the quest (goals,
    reward, where it plays): the engine stores it and never reads it. The seed's templates are kept up to date from the file; promoted
    ones belong to the database and the seed never touches them."""

    __tablename__ = "quest_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16), default=SEEDED)
    # A team is offered the template when the quest's level (the team's, plus the quest's relative difficulty) is in this range.
    min_level: Mapped[int] = mapped_column(Integer, default=1)
    max_level: Mapped[int] = mapped_column(Integer, default=999)
    # Its share in the draw among the templates that fit.
    weight: Mapped[int] = mapped_column(Integer, default=1)
    spec: Mapped[dict] = mapped_column(JSON)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # The rotating quest slot (``quests.rotation``) this template belongs to, or None for a weekly quest: a slot's templates are never
    # drawn for the weekly quest, and the levels above mean nothing to a slot (its quest is the same for every team).
    slot: Mapped[str | None] = mapped_column(String(64), index=True)


class WeeklyQuest(Timestamps, Base):
    """A team's quest for one week: at most one. A generated one is never deleted (a team that is deleted leaves its quests behind,
    owned by nobody) so an admin can still promote it to a template."""

    __tablename__ = "weekly_quests"
    __table_args__ = (UniqueConstraint("team_id", "week"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), index=True)
    # Counted from the Monday 1970-01-05 00:00 UTC (``quests.weekly.week_of``).
    week: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16), index=True)
    template_id: Mapped[int | None] = mapped_column(ForeignKey("quest_templates.id"))
    # Set once an admin has made this generated quest a template (the template it became).
    promoted_template_id: Mapped[int | None] = mapped_column(ForeignKey("quest_templates.id"))
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer)
    spec: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default=OFFERED)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
