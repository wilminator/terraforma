"""The marketplace's listings. A listing is one offer of goods at a guild for a fixed price; the goods wait in an escrow deposit."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

OPEN = "open"
SOLD = "sold"
CANCELLED = "cancelled"
EXPIRED = "expired"

FIXED = "fixed"
AUCTION = "auction"


class Listing(Timestamps, Base):
    """One offer. ``guild`` is the hall it is posted at, ``escrow_id`` the deposit holding the goods and ``seller_team_id`` the team
    that gets the price. It costs ``price_gold`` gold and ``price_tokens`` Challenge Tokens, both at once, and is open until
    ``expires_at`` (wall clock, whole seconds). ``state`` goes from ``open`` to ``sold`` (``buyer_team_id`` bought it and the goods
    went to them as a parcel), ``cancelled`` (the seller took it back) or ``expired`` (nobody bought it in time and the goods went back
    to the seller) and never changes again.

    An *auction* (``kind``) asks ``price_gold`` as its starting bid, in gold only. ``bid_gold`` is the highest bid so far, from
    ``bid_team_id``, who wants the goods delivered to ``bid_guild``; the bid and the delivery fee ``bid_fee`` sit in the escrow deposit
    ``bid_escrow_id`` until the bidder is outbid (it goes back) or the auction closes (the seller gets the bid, the fee goes to the guild)."""

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
    kind: Mapped[str] = mapped_column(String(8), default=FIXED)
    bid_gold: Mapped[int] = mapped_column(BigInteger, default=0)
    bid_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    bid_guild: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bid_fee: Mapped[int] = mapped_column(BigInteger, default=0)
    bid_escrow_id: Mapped[int | None] = mapped_column(ForeignKey("escrows.id"), nullable=True)
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # wall clock, whole seconds
