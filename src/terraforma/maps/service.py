"""Putting a game's maps (``maps.json``, checked by ``content.schema``) into a world."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.schema import MapSeed
from ..db.dialect import upsert
from ..models import Map
from ..npcs.models import NpcTalk
from .models import EDGE, MapObject


def _fields(row: MapSeed) -> dict:
    zones = [zone.model_dump(mode="json") for zone in row.zones]
    return {
        "title": row.name,
        "width": row.width,
        "height": row.height,
        "wrap_x": row.wrap_x,
        "wrap_y": row.wrap_y,
        "safe_steps": row.safe_steps,
        "tileset": [tile.model_dump(mode="json") for tile in row.tileset],
        "tiles": [list(line) for line in row.tiles],
        "zones": zones,
        "zone_tiles": [list(line) for line in row.zone_tiles] if row.zone_tiles is not None else [[0] * row.width for _ in range(row.height)],
    }


def _objects(row: MapSeed) -> dict[str, dict]:
    """The rows of the map's objects and edge events, by key."""
    found = {
        each.key: {"name": each.name, "kind": each.kind, "action": each.action, "x": each.x, "y": each.y, "dialog": each.script, "edge": None}
        for each in row.objects
    }
    for direction, script in row.edges.items():
        found[f"edge-{direction}"] = {"name": direction, "kind": EDGE, "action": "walk", "x": 0, "y": 0, "dialog": script, "edge": direction}
    return found


def _seen(fields: dict) -> tuple:
    """What a page can tell about an object (everything but its script): when it changes, the map's revision goes up."""
    return tuple(fields[name] for name in ("name", "kind", "action", "x", "y", "edge"))


async def _apply_objects(session: AsyncSession, found: Map, row: MapSeed) -> bool:
    """Makes the map's objects and edge events match the seed row: new ones added, known ones (by key) rewritten, ones the
    row no longer has removed (with any conversation a hero had with them). Returns whether anything a page can see changed."""
    wanted = _objects(row)
    await session.flush()  # the rows below are written with bulk SQL
    have = {each.key: each for each in (await session.scalars(select(MapObject).where(MapObject.map_id == found.id))).all()}
    changed = {key for key in have if key not in wanted} | {
        key for key, fields in wanted.items() if key not in have or _seen(fields) != _seen({name: getattr(have[key], name) for name in fields})
    }
    for key, fields in wanted.items():
        await upsert(session, MapObject.__table__, {"map_id": found.id, "key": key, **fields}, ["map_id", "key"])
    gone = [each.id for key, each in have.items() if key not in wanted]
    if gone:
        await session.execute(delete(NpcTalk).where(NpcTalk.object_id.in_(gone)))
        await session.execute(delete(MapObject).where(MapObject.id.in_(gone)))
    return bool(changed)


async def apply_maps(session: AsyncSession, world_id: int, rows: list[MapSeed]) -> int:
    """Makes the world's maps match $rows: a new map is added, a known one (by key) is rewritten and its ``revision`` goes up
    if anything about it changed. A map the rows no longer list is left alone (things may still stand on it), and so is the
    default hub unless the rows name it. Safe to run on every start and from several processes at once. Returns how many maps."""
    for row in rows:
        await upsert(session, Map.__table__, {"world_id": world_id, "name": row.key}, ["world_id", "name"])
        found = await session.scalar(select(Map).where(Map.world_id == world_id, Map.name == row.key).execution_options(populate_existing=True))
        fields = _fields(row)
        grid_changed = not all(getattr(found, name) == value for name, value in fields.items())
        objects_changed = await _apply_objects(session, found, row)
        if not (grid_changed or objects_changed):
            continue
        # A map with no grid yet has just been made (or is the default hub): its first content is revision 1.
        if found.tiles is not None:
            found.revision += 1
        for name, value in fields.items():
            setattr(found, name, value)
    await session.flush()
    return len(rows)
