"""What a guild holds in escrow. A row is one deposit by one team; its items are the rows beside it."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

HELD = "held"
REFUNDED = "refunded"
FORFEITED = "forfeited"


class Escrow(Timestamps, Base):
    """One deposit. ``guild`` names the hall that holds it (the place the deposit exists), ``purpose`` is the game's word for why
    (a breach, a listing, a delivery) and ``ref`` whatever the game wants to find it by again (a contract's key). The depositor
    is a team, like standing and quests. A deposit is settled once: ``state`` goes from ``held`` to ``refunded`` (it went back to
    the depositor) or ``forfeited`` (it went to ``settled_to``, another team), and never changes again."""

    __tablename__ = "escrows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(64), default="", index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    gold: Mapped[int] = mapped_column(BigInteger, default=0)
    tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    state: Mapped[str] = mapped_column(String(10), default=HELD, index=True)
    settled_to: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True)
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # wall clock, whole seconds


class EscrowItem(Base):
    """A stack of one kind of item in a deposit."""

    __tablename__ = "escrow_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    escrow_id: Mapped[int] = mapped_column(ForeignKey("escrows.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer)
