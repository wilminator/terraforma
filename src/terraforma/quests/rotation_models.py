"""Rotating quests: a slot (say a guild's rank S promotion) holds one quest at a time and changes it after a random while; each team
that takes the current quest has its own progress row. Times are whole seconds since 1970 (``wallclock.timestamp``)."""

from sqlalchemy import JSON, BigInteger, Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

#: Where a slot's quest for a period came from: a template tagged for the slot, the game's ``generate``, or nothing (the game made none).
FROM_TEMPLATE, GENERATED, NOTHING = "template", "generated", "nothing"
#: Where a team stands with a slot quest: offered (not taken), active (taken, the clock is running), completed, failed, lapsed (never
#: taken before the quest rotated out) or ended (the world ended the quest while the team held it).
OFFERED, ACTIVE, COMPLETED, FAILED, LAPSED, ENDED = "offered", "active", "completed", "failed", "lapsed", "ended"
#: Why a quest failed: the team ran out of time, did badly (the game says so), or the world ended it (never penalized by default).
TIMEOUT, POOR_PERFORMANCE, WORLD = "timeout", "poor_performance", "world"
#: Why an obligation lapsed: a member team left the period's quest alone (it never took it, or it was offered and let go).
IGNORED = "ignored"


class QuestSlot(Timestamps, Base):
    """A place where one quest at a time is offered and the quest changes now and then (the game's seed lists them). The engine knows
    no guilds or ranks: ``guild`` and ``rank`` only tell the game's dialogs which slot to ask about. A period lasts a whole number of
    seconds drawn from ``min_seconds``..``max_seconds`` with a stream of the world's seed, so the schedule replays and a higher tier
    simply gets a longer minimum."""

    __tablename__ = "quest_slots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    guild: Mapped[str] = mapped_column(String(64), default="")
    rank: Mapped[int] = mapped_column(Integer, default=0)
    min_seconds: Mapped[int] = mapped_column(Integer)
    max_seconds: Mapped[int] = mapped_column(Integer)
    # How long a team that took the quest may keep at it after the quest rotated out.
    grace_seconds: Mapped[int] = mapped_column(Integer, default=0)
    # The wall-clock time limit: how long a team has to finish the quest once it takes it.
    limit_seconds: Mapped[int] = mapped_column(Integer)
    # When the first period began (the moment the slot first reached the database).
    anchor: Mapped[int] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # An obligation slot is a duty of the guild's members: a team with a standing in ``guild`` that leaves a period's quest undone is penalized.
    obligation: Mapped[bool] = mapped_column(Boolean, default=False)


class SlotQuest(Timestamps, Base):
    """The quest a slot held for one period. Made the first time anyone asks during the period (no scheduler runs anywhere), and kept:
    a period nobody asked about has no row. ``spec`` is the game's own description, stored and handed back, never read."""

    __tablename__ = "slot_quests"
    __table_args__ = (UniqueConstraint("slot_id", "seq"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slot_id: Mapped[int] = mapped_column(ForeignKey("quest_slots.id"), index=True)
    # Counts the slot's periods from 0; periods nobody asked about leave gaps.
    seq: Mapped[int] = mapped_column(Integer)
    starts_at: Mapped[int] = mapped_column(BigInteger)
    ends_at: Mapped[int] = mapped_column(BigInteger)
    # True once the world ended the quest before its time (``ends_at`` is then when it ended).
    ended_early: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(16))
    template_id: Mapped[int | None] = mapped_column(ForeignKey("quest_templates.id"))
    name: Mapped[str] = mapped_column(String(64), default="")
    spec: Mapped[dict] = mapped_column(JSON)


class TeamSlotQuest(Timestamps, Base):
    """A team's progress with one period's quest: at most one row per team per period."""

    __tablename__ = "team_slot_quests"
    __table_args__ = (UniqueConstraint("team_id", "slot_quest_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    slot_quest_id: Mapped[int] = mapped_column(ForeignKey("slot_quests.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=OFFERED)
    started_at: Mapped[int | None] = mapped_column(BigInteger)
    # When the team's time runs out: the limit after it started, or the end of the grace period, whichever is first.
    deadline: Mapped[int | None] = mapped_column(BigInteger)
    finished_at: Mapped[int | None] = mapped_column(BigInteger)
    failure: Mapped[str | None] = mapped_column(String(24))
