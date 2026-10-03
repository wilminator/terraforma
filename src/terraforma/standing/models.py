"""Standing statuses: a status token that lasts between fights, on a hero, a team or a party."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

HERO, TEAM, PARTY = "hero", "team", "party"
KINDS = (HERO, TEAM, PARTY)


class StandingStatus(Timestamps, Base):
    """A status ($status, a key in the content's statuses) on a hero, a team or a party ($target_kind and $target_id: no link,
    like a relationship's sides, so whatever deletes the target also calls ``standing.service.forget``). It ends at
    $ends_at, a wall-clock time (None: until something removes it). $unremovable: buff-cancelling effects cannot take it off.
    $ends_party: a team's status that is a guest pass: when it ends, the team leaves its party (and it is gone when the team leaves
    on its own). Placing the same status on the same target again renews it."""

    __tablename__ = "standing_statuses"
    __table_args__ = (UniqueConstraint("target_kind", "target_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[int] = mapped_column(Integer, index=True)
    status: Mapped[str] = mapped_column(String(64))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    unremovable: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    ends_party: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
