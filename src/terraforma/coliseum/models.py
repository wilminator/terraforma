"""Coliseum bouts: one team challenges another to a fight in the arena."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

OFFERED = "offered"
FOUGHT = "fought"
DECLINED = "declined"
WITHDRAWN = "withdrawn"
EXPIRED = "expired"


class Bout(Timestamps, Base):
    """A bout. Team ``challenger_id`` asked team ``challenged_id`` to fight; the offer ends at ``expires_at`` (wall-clock seconds). ``state`` goes from
    ``offered`` to ``fought`` (accepted; ``fight_id`` is the fight, which is public once it finishes), ``declined``, ``withdrawn`` (the challenger
    took it back) or ``expired``."""

    __tablename__ = "coliseum_bouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    challenger_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    challenged_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    state: Mapped[str] = mapped_column(String(16), default=OFFERED, index=True)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)  # (wall-clock seconds, ``wallclock.timestamp``)
    fight_id: Mapped[int | None] = mapped_column(ForeignKey("fights.id"), nullable=True, index=True)
