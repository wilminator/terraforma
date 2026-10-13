"""The queue of volunteers, their lockouts, and who sits in which fight.

None of these stands anywhere on a map: a volunteer waits for any fight, and a seat is part of a fight (which has its place).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, false
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class MonsterVolunteer(Base):
    """An account waiting to command the monsters of the next fight that can take them. One place per account; the queue runs
    longest-waiting first (``queued_at``). A volunteer who has not checked in for ``Rules.monster_checkin_seconds`` is not
    waiting any more, and the next seating drops them."""

    __tablename__ = "monster_volunteers"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    checked_in: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MonsterLockout(Base):
    """An account kept out of the queue until ``until`` (an hour after abandoning a fight their monsters then lost, by the
    default rules; set when the fight's seats are settled)."""

    __tablename__ = "monster_lockouts"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MonsterSeat(Base):
    """The account that commands a fight's monster ``party``. Written once, when the fight is created: a volunteer is never
    seated into a running fight."""

    __tablename__ = "monster_seats"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
    party: Mapped[int] = mapped_column(Integer, default=1)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    seated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # How many rounds the player gave their monsters no command at all (``Rules.monster_absent`` judges each), and whether the AI plays the
    # monsters now: until the player commands again or says they are back.
    missed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    absent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # An admin sat in a running fight (``service.take_over``): earns nothing, whatever the rules pay.
    admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Whether the seat was settled when the fight ended (tokens paid, a lockout set): once.
    settled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
