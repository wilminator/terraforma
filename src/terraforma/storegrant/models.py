"""What the game's store granted, and the items waiting in a player's parcel."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..db.dialect import ExactJSON


class StoreGrant(Timestamps, Base):
    """One grant the store made: the store's idempotency key (unique, so a retried call grants once), the account, the
    Challenge Tokens (negative: taken), the items (content key -> quantity) and the message the player was sent. The exact text of
    ``items`` matters (a retry is compared with it), so it is ``ExactJSON``."""

    __tablename__ = "store_grants"
    __table_args__ = (UniqueConstraint("idempotency_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    items: Mapped[dict] = mapped_column(ExactJSON)
    message: Mapped[str] = mapped_column(String(1000), default="")


class Parcel(Timestamps, Base):
    """Items the store granted that the player has not taken yet: one row per account and item. It is only a holding place;
    a game moves parcels into its own storage (the team's inventory, say) by its own rules."""

    __tablename__ = "parcels"
    __table_args__ = (UniqueConstraint("account_id", "item_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer, default=1)
