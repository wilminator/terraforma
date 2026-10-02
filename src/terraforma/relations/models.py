"""Relationships: one row per direction, so A's view of B is separate from B's view of A."""

from sqlalchemy import Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

NOTE_MAX = 280


class Relationship(Timestamps, Base):
    """How the subject feels about the object. The score and the note are the subject's, and no one else's to read.

    The two sides are a kind and an id rather than links, so any kind of side (a team now, an alliance later) fits the
    same table; ``service.forget`` removes a side's rows when it goes."""

    __tablename__ = "relationships"
    __table_args__ = (UniqueConstraint("subject_kind", "subject_id", "object_kind", "object_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subject_kind: Mapped[str] = mapped_column(String(8))
    subject_id: Mapped[int] = mapped_column(Integer, index=True)
    object_kind: Mapped[str] = mapped_column(String(8))
    object_id: Mapped[int] = mapped_column(Integer, index=True)
    score: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(String(NOTE_MAX), default="")
