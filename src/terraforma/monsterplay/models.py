"""The queue of volunteers, their lockouts, and who sits in which fight.

None of these stands anywhere on a map: a volunteer waits for any fight, and a seat is part of a fight (which has its place).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class MonsterGuest(Base):
    """Someone who is not logged in and plays monsters anyway (slice d). Not an account: only a random token, of which just the hash
    is kept, so the page that was given it is the only one who can act as the guest. It lapses ``Rules.monster_guest_seconds`` after
    it was last used, and a guest has no name, no tokens earned and no lockout."""

    __tablename__ = "monster_guests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class MonsterVolunteer(Base):
    """An account (or a guest) waiting to command the monsters of the next fight that can take them. One place each; the queue runs
    longest-waiting first (``queued_at``). A volunteer who has not checked in for ``Rules.monster_checkin_seconds`` is not
    waiting any more, and the next seating drops them. Exactly one of ``account_id`` and ``guest_id`` is set."""

    __tablename__ = "monster_volunteers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), unique=True, nullable=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("monster_guests.id"), unique=True, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    checked_in: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MonsterLockout(Base):
    """An account kept out of the queue until ``until`` (an hour after abandoning a fight their monsters then lost, by the
    default rules; set when the fight's seats are settled)."""

    __tablename__ = "monster_lockouts"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True, autoincrement=False)
    until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MonsterSeat(Base):
    """The account (or guest) that commands a fight's monster ``party``. Written once, when the fight is created: a volunteer is never
    seated into a running fight. Exactly one of ``account_id`` and ``guest_id`` is set."""

    __tablename__ = "monster_seats"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
    party: Mapped[int] = mapped_column(Integer, default=1)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), index=True, nullable=True)
    guest_id: Mapped[int | None] = mapped_column(ForeignKey("monster_guests.id"), index=True, nullable=True)
    seated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # How many rounds the player gave their monsters no command at all (``Rules.monster_absent`` judges each), and whether the AI plays the
    # monsters now: until the player commands again or says they are back.
    missed: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    absent: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # An admin sat in a running fight (``service.take_over``): earns nothing, whatever the rules pay.
    admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Whether the seat was settled when the fight ended (tokens paid, a lockout set): once.
    settled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    @property
    def holder(self) -> int:
        """Who plays the seat, as the fight's calls see it: the account's id, or for a guest the guest's id made negative (an account
        id is never negative, so the two never meet)."""
        return self.account_id if self.account_id is not None else -self.guest_id
