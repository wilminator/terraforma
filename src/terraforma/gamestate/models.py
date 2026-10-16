"""The tables of planned and live state: committed snapshots, the one live pointer and the one editable draft."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, false
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..db.dialect import ExactJSON


class Snapshot(Timestamps, Base):
    """One committed settings document, never changed afterwards. ``hash`` is the hash of its canonical text, so equal documents
    have equal hashes (fights and recordings store it). The document is ``ExactJSON`` because its exact text is what was hashed.
    ``committed_by`` is the admin (None for the first, empty one the engine makes)."""

    __tablename__ = "state_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hash: Mapped[str] = mapped_column(String(64), index=True)
    document: Mapped[dict] = mapped_column(ExactJSON)
    committed_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    note: Mapped[str] = mapped_column(String(200), default="", server_default="")


class LiveState(Timestamps, Base):
    """The one row (``id`` 1) that says which snapshot is live. Every server compares ``snapshot_id`` to notice a commit."""

    __tablename__ = "state_live"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("state_snapshots.id"))


class PlannedState(Timestamps, Base):
    """The one row (``id`` 1) holding the editable draft. ``revision`` counts the edits, so two admins editing at once can't
    overwrite each other unseen. ``frozen`` is set by an import and cleared by any edit (nobody sets it by hand): a frozen plan is
    exactly what was imported, which is what staged maintenance requires."""

    __tablename__ = "state_planned"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document: Mapped[dict] = mapped_column(ExactJSON)
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    edited_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
