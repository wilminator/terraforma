"""The marketplace's listings. A listing is one offer of goods at a guild for a fixed price; the goods wait in an escrow deposit."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

OPEN = "open"
SOLD = "sold"
CANCELLED = "cancelled"
EXPIRED = "expired"


class Listing(Timestamps, Base):
    """One offer. ``guild`` is the hall it is posted at, ``escrow_id`` the deposit holding the goods and ``seller_team_id`` the team
    that gets the price. It costs ``price_gold`` gold and ``price_tokens`` Challenge Tokens, both at once, and is open until
    ``expires_at`` (wall clock, whole seconds). ``state`` goes from ``open`` to ``sold`` (``buyer_team_id`` bought it and the goods
    went to them as a parcel), ``cancelled`` (the seller took it back) or ``expired`` (nobody bought it in time and the goods went back
    to the seller) and never changes again."""

    __tablename__ = "listings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild: Mapped[str] = mapped_column(String(64), index=True)
    seller_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True, unique=True)
    price_gold: Mapped[int] = mapped_column(BigInteger, default=0)
    price_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[int] = mapped_column(BigInteger, index=True)
    state: Mapped[str] = mapped_column(String(10), default=OPEN, index=True)
    buyer_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # wall clock, whole seconds
