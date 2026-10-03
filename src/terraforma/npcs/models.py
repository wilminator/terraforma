"""An NPC, and the conversation a hero is in the middle of."""

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located


class Npc(Located, Timestamps, Base):
    """An NPC stands on a map at a tile. ``dialog`` is its text (``npcs.script``); the activities it offers (a store, an inn,
    a church) are tags in that text. ``counter`` is the row of counter tiles it serves across, as ``[[x, y], ...]``: a hero
    talks from a tile next to one of them (or on it), and the NPC stands next to the same tile. ``key`` is the game's own
    name for it, stable across releases."""

    __tablename__ = "npcs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    dialog: Mapped[str] = mapped_column(Text)
    counter: Mapped[list] = mapped_column(JSON, default=list)


class NpcTalk(Timestamps, Base):
    """A hero in the middle of a conversation: at most one each. ``pos`` is where the text goes on from, ``prompt`` what the
    hero was last asked (None when it was not waiting on anything)."""

    __tablename__ = "npc_talks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hero_id: Mapped[int] = mapped_column(ForeignKey("heroes.id"), unique=True)
    npc_id: Mapped[int] = mapped_column(ForeignKey("npcs.id"), index=True)
    pos: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[dict | None] = mapped_column(JSON, nullable=True)
