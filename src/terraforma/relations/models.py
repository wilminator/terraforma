"""Relationships: one row per direction, so A's view of B is separate from B's view of A."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
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


HELPED, HARMED, BOTH = "helped", "harmed", "both"
PENDING, ANSWERED, DISMISSED = "pending", "answered", "dismissed"


class RatingPrompt(Timestamps, Base):
    """A question put to a team after a fight: another player team helped or harmed it, and it had no opinion of that team
    (``relations.ratings``). The sides are team ids without links, like a relationship's, and go with the team."""

    __tablename__ = "rating_prompts"
    __table_args__ = (UniqueConstraint("fight_id", "subject_team_id", "object_team_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), index=True)
    subject_team_id: Mapped[int] = mapped_column(Integer, index=True)
    object_team_id: Mapped[int] = mapped_column(Integer, index=True)
    # What the other team did: helped, harmed or both; "asked" when only the game's rule asked (``AskPlayer``).
    interaction: Mapped[str] = mapped_column(String(8))
    state: Mapped[str] = mapped_column(String(10), default=PENDING)
    # When the game's rule asked (``Rules.relation_moved`` returned ``AskPlayer``): the change it suggests and why. Else none.
    suggested: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(String(255), default="")
