"""The stacks kept in storage. The owner is a kind and an id (a team, an account) that the game's ``Storage`` rule picks."""

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base


class StorageItem(Base):
    """A stack of one kind of item in an owner's storage, at a position (0, 1, 2, ... with no gaps; storage.service keeps it so)."""

    __tablename__ = "storage_items"
    __table_args__ = (UniqueConstraint("owner_kind", "owner_id", "position"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(16))  # "team" or "account" (a game may add its own kinds)
    owner_id: Mapped[int] = mapped_column(Integer)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    position: Mapped[int] = mapped_column(Integer)
    qty: Mapped[int] = mapped_column(Integer, default=1)
