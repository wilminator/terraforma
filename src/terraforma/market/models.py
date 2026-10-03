"""A shop: a place on a map where a hero standing on its tile may buy and sell."""

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located


class Shop(Located, Timestamps, Base):
    """A shop stands on a map at a tile, like a hero. What it sells is not stored: it is a function of the place's economy
    level (``Market.stock``). ``key`` is the game's own name for it, stable across releases."""

    __tablename__ = "shops"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
