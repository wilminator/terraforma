"""Manual quests: events an admin writes by hand (an alliance competition, a coliseum tournament) and teams enter while it is open. Times are
whole seconds since 1970 (``wallclock.timestamp``)."""

from sqlalchemy import JSON, BigInteger, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: Where an event stands: a draft (an admin is still writing it, nobody sees it), published (teams see it, and enter while it is open) or
#: cancelled (an admin called it off). That it is over is read from its times, never stored.
DRAFT, PUBLISHED, CANCELLED = "draft", "published", "cancelled"
#: Where a team stands in an event it entered: active (the clock is running), completed, failed, or ended (the event was cancelled under it).
ACTIVE, COMPLETED, FAILED, ENDED = "active", "completed", "failed", "ended"
#: Why a team failed: it ran out of time, or did badly (the game's dungeon or an admin says so). A cancelled event is nobody's failure.
TIMEOUT, POOR_PERFORMANCE, CANCELLED_BY_ADMIN = "timeout", "poor_performance", "cancelled"


class ManualQuest(Timestamps, Base):
    """A quest an admin wrote. ``kind`` is a label the game uses (``alliance``, ``coliseum``); the engine reads neither it nor ``spec`` (the
    game's own description: goals, reward, which dungeon). Teams may enter from ``opens_at`` until ``closes_at``; a team that entered has
    ``limit_seconds`` to finish (0: until ``ends_at``) and never longer than ``ends_at``, when the event is over. It may stand somewhere on
    a map (the coliseum's arena), for the map and the game's dialogs."""

    __tablename__ = "manual_quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(32), default="")
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(Text, default="")
    spec: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(12), default=DRAFT, index=True)
    opens_at: Mapped[int] = mapped_column(BigInteger)
    closes_at: Mapped[int] = mapped_column(BigInteger)
    ends_at: Mapped[int] = mapped_column(BigInteger)
    limit_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # How many teams may enter in all (0: no limit).
    max_entries: Mapped[int] = mapped_column(Integer, default=0)
    map_id: Mapped[int | None] = mapped_column(ForeignKey("maps.id"), index=True)
    x: Mapped[int | None] = mapped_column(Integer)
    y: Mapped[int | None] = mapped_column(Integer)
    # The admin who wrote it; kept as a plain id so a deleted account does not take the event's record with it.
    author_id: Mapped[int | None] = mapped_column(Integer)


class ManualEntry(Timestamps, Base):
    """A team's entry in an event: at most one per team. ``score`` ranks the entries of a competition (the dungeon or an admin sets it)."""

    __tablename__ = "manual_entries"
    __table_args__ = (UniqueConstraint("quest_id", "team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    quest_id: Mapped[int] = mapped_column(ForeignKey("manual_quests.id"), index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default=ACTIVE)
    score: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[int] = mapped_column(BigInteger)
    deadline: Mapped[int] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    failure: Mapped[str | None] = mapped_column(String(24))
