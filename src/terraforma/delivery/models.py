"""Goods on their way. A shipment is one parcel for one team at one guild, with a time it arrives."""

from sqlalchemy import BigInteger, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps

TRANSIT = "transit"
COLLECTED = "collected"
CANCELLED = "cancelled"


class Shipment(Timestamps, Base):
    """One parcel. ``guild`` names the hall it is delivered to (where the parcel exists once it has arrived), ``purpose`` is the game's
    word for why (a purchase, a quest) and ``ref`` whatever it wants to find the parcel by. ``from_team_id`` is the sending team, or
    none for goods the game made (a quest giver's reward, say). The parcel is ready to collect once the wall clock passes
    ``arrives_at``; ``state`` goes from ``transit`` to ``collected`` (the receiving team has it) or ``cancelled`` (it went back to the
    sender, or vanished if the game made it) and never changes again."""

    __tablename__ = "shipments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    guild: Mapped[str] = mapped_column(String(64), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    ref: Mapped[str] = mapped_column(String(64), default="", index=True)
    from_team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"), nullable=True, index=True)
    to_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    gold: Mapped[int] = mapped_column(BigInteger, default=0)
    arrives_at: Mapped[int] = mapped_column(BigInteger, index=True)  # wall clock, whole seconds
    state: Mapped[str] = mapped_column(String(10), default=TRANSIT, index=True)
    settled_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # wall clock, whole seconds


class ShipmentItem(Base):
    """A stack of one kind of item in a parcel."""

    __tablename__ = "shipment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    shipment_id: Mapped[int] = mapped_column(ForeignKey("shipments.id"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"))
    qty: Mapped[int] = mapped_column(Integer)
