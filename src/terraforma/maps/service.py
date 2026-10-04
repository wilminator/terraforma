"""Putting a game's maps (``maps.json``, checked by ``content.schema``) into a world."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.schema import MapSeed
from ..db.dialect import upsert
from ..models import Map


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


async def apply_maps(session: AsyncSession, world_id: int, rows: list[MapSeed]) -> int:
    """Makes the world's maps match $rows: a new map is added, a known one (by key) is rewritten and its ``revision`` goes up
    if anything about it changed. A map the rows no longer list is left alone (things may still stand on it), and so is the
    default hub unless the rows name it. Safe to run on every start and from several processes at once. Returns how many maps."""
    for row in rows:
        await upsert(session, Map.__table__, {"world_id": world_id, "name": row.key}, ["world_id", "name"])
        found = await session.scalar(select(Map).where(Map.world_id == world_id, Map.name == row.key).execution_options(populate_existing=True))
        fields = _fields(row)
        if all(getattr(found, name) == value for name, value in fields.items()):
            continue
        # A map with no grid yet has just been made (or is the default hub): its first content is revision 1.
        if found.tiles is not None:
            found.revision += 1
        for name, value in fields.items():
            setattr(found, name, value)
    await session.flush()
    return len(rows)
