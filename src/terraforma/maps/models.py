"""Things that stand on a map and run an event script when a hero uses them: chests, doors, signs, and a map's edges."""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
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
