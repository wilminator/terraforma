"""The trade ledger: every gift of gold or an item between heroes, kept after the heroes are gone."""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, utcnow


class TradeRecord(Base):
    """One thing given. The hero ids and names are copies, not links, so a deleted hero leaves its history readable."""

    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    giver_id: Mapped[int] = mapped_column(Integer, index=True)
    receiver_id: Mapped[int] = mapped_column(Integer, index=True)
    giver_name: Mapped[str] = mapped_column(String(24))
    receiver_name: Mapped[str] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(8))  # "gold" or "item"
    item_key: Mapped[str] = mapped_column(String(64), default="")
    qty: Mapped[int] = mapped_column(BigInteger)  # the amount of gold, or how many of the item
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
