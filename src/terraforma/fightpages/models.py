"""Which fights a game has marked public."""

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps


class PublicFight(Timestamps, Base):
    """A finished fight the game marked public (``Rules.public_fight``): anyone may watch it, and the coliseum page lists it.
    A public fight is kept for good (the housekeeping that deletes old fights leaves it alone)."""

    __tablename__ = "public_fights"

    fight_id: Mapped[int] = mapped_column(ForeignKey("fights.id"), primary_key=True, autoincrement=False)
