"""Things that stand on a map and run an event script when a hero uses them: chests, doors, signs, and a map's edges."""

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db.base import Base, Timestamps
from ..world.location import Located

NORTH, SOUTH, EAST, WEST = "north", "south", "east", "west"
#: The four edges of a map, and the step that leaves the map over each.
EDGES = {NORTH: (0, -1), SOUTH: (0, 1), EAST: (1, 0), WEST: (-1, 0)}
#: The ``kind`` of the row that holds an edge's script.
EDGE = "edge"


class MapObject(Located, Timestamps, Base):
    """A map object stands at a tile and runs ``dialog`` (the same text as an NPC's, ``npcs.script``) when a hero uses it. The
    ``action`` is the kind of reach it takes (``Reach.object``): ``open`` for a chest or a door, ``search`` for a bush, or a
    game's own. ``kind`` is the game's word for what it is (chest, door, sign): the engine draws no difference, the page may.

    The row of a map's edge event has the kind ``edge`` and the direction in ``edge``: it stands nowhere, and runs when a
    party walks off that edge of the map (``maps.walking``). ``key`` is the game's own name for the object, stable across
    releases, and unique on its map."""

    __tablename__ = "map_objects"
    __table_args__ = (UniqueConstraint("map_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))
    dialog: Mapped[str] = mapped_column(Text)
    edge: Mapped[str | None] = mapped_column(String(5), nullable=True)


class SavedMap(Timestamps, Base):
    """A map kept for reuse (``maps.library``): the content of a map a game generated, under the game's own ``key``, so the next place that
    wants one like it is made from this copy instead of being generated again. ``objects`` are the map's object and edge rows (without their
    ids or the map), ``status`` says whether an admin let it be reused (see ``maps.library``) and ``uses`` counts the maps made from it."""

    __tablename__ = "saved_maps"
    __table_args__ = (UniqueConstraint("world_id", "key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    world_id: Mapped[int] = mapped_column(ForeignKey("worlds.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(64), default="", server_default="")
    width: Mapped[int] = mapped_column(Integer, default=1)
    height: Mapped[int] = mapped_column(Integer, default=1)
    wrap_x: Mapped[bool] = mapped_column(Boolean, default=False)
    wrap_y: Mapped[bool] = mapped_column(Boolean, default=False)
    safe_steps: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    tileset: Mapped[list | None] = mapped_column(JSON)
    tiles: Mapped[list | None] = mapped_column(JSON)
    zones: Mapped[list | None] = mapped_column(JSON)
    zone_tiles: Mapped[list | None] = mapped_column(JSON)
    objects: Mapped[list] = mapped_column(JSON, default=list)
    #: ``proposed`` (waiting for an admin; never copied), ``approved`` (copied for later places) or ``rejected`` (never copied).
    status: Mapped[str] = mapped_column(String(16), default="proposed", server_default="proposed")
    uses: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
