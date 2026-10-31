"""Maps kept for reuse: a map a game generated (a dungeon, a cellar, an instance) is saved under the game's own key, and the next place that
wants one like it is made from the copy instead of being generated again.

The engine generates no maps; the game does (for instance in ``Needs.dungeon``). This is the memory it can use:

    game_map = await library.reuse_or_make(session, world_id, "crypt-small", name=f"crypt-{proposal_id}", make=build_crypt)

``make`` is called only when nothing is saved under the key; its map is then saved, so the next call copies it. What a copy holds is the map's
grids, flags and its object and edge rows (chests, doors, signs and their scripts). It holds none of what happens on the map: NPCs belong to
the seed, and parties, fights and instances are never copied. A copy is a map of its own with its own name, so changing one never changes the
saved copy or another map made from it.
"""

from collections.abc import Awaitable, Callable

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Map
from ..world.location import check_map_name
from .models import MapObject, SavedMap


class LibraryError(ValueError):
    """Something the caller can fix: the message says what."""


def _check_key(key: str) -> str:
    if not key or len(key) > 64:
        raise LibraryError("A saved map's key is 1 to 64 characters.")
    return key


async def find(session: AsyncSession, world_id: int, key: str) -> SavedMap | None:
    return await session.scalar(select(SavedMap).where(SavedMap.world_id == world_id, SavedMap.key == key))


async def listing(session: AsyncSession, world_id: int) -> list[SavedMap]:
    return list((await session.scalars(select(SavedMap).where(SavedMap.world_id == world_id).order_by(SavedMap.key))).all())


async def save(session: AsyncSession, game_map: Map, key: str, replace: bool = False) -> SavedMap:
    """Keeps a copy of the map's content under $key. A key already saved is refused unless $replace (maps made from the old copy keep what
    they were made with)."""
    _check_key(key)
    saved = await find(session, game_map.world_id, key)
    if saved is not None and not replace:
        raise LibraryError(f"There is already a saved map {key!r}.")
    objects = (await session.scalars(select(MapObject).where(MapObject.map_id == game_map.id).order_by(MapObject.key))).all()
    values = {
        "title": game_map.title, "width": game_map.width, "height": game_map.height, "wrap_x": game_map.wrap_x, "wrap_y": game_map.wrap_y,
        "safe_steps": game_map.safe_steps, "tileset": game_map.tileset, "tiles": game_map.tiles, "zones": game_map.zones,
        "zone_tiles": game_map.zone_tiles,
        "objects": [{"key": each.key, "name": each.name, "kind": each.kind, "action": each.action, "dialog": each.dialog, "edge": each.edge,
                     "x": each.x, "y": each.y} for each in objects],
    }
    if saved is None:
        saved = SavedMap(world_id=game_map.world_id, key=key, **values)
        session.add(saved)
    else:
        for name, value in values.items():
            setattr(saved, name, value)
    await session.flush()
    return saved


async def instantiate(session: AsyncSession, saved: SavedMap, name: str, title: str | None = None) -> Map:
    """Makes a new map called $name in the saved copy's world from it."""
    try:
        check_map_name(name)
    except ValueError as error:
        raise LibraryError(str(error)) from error
    if await session.scalar(select(Map.id).where(Map.world_id == saved.world_id, Map.name == name)) is not None:
        raise LibraryError(f"There is already a map {name!r}.")
    made = Map(world_id=saved.world_id, name=name, title=saved.title if title is None else title, width=saved.width, height=saved.height,
               wrap_x=saved.wrap_x, wrap_y=saved.wrap_y, safe_steps=saved.safe_steps, tileset=saved.tileset, tiles=saved.tiles,
               zones=saved.zones, zone_tiles=saved.zone_tiles)
    session.add(made)
    await session.flush()
    for each in saved.objects:
        session.add(MapObject(map_id=made.id, **each))
    saved.uses += 1
    await session.flush()
    return made


async def reuse_or_make(session: AsyncSession, world_id: int, key: str, name: str, make: Callable[[], Awaitable[Map]], title: str | None = None) -> Map:
    """A map called $name like the one saved under $key: a copy when there is one; otherwise ``make()`` (the game generates a map, with the
    name $name, and this keeps a copy of it under $key for the next call) is called and its map is returned."""
    saved = await find(session, world_id, _check_key(key))
    if saved is not None:
        return await instantiate(session, saved, name, title)
    made = await make()
    await save(session, made, key)
    return made


async def forget(session: AsyncSession, world_id: int, key: str) -> bool:
    """Drops the saved copy (maps made from it stay). Returns whether there was one."""
    result = await session.execute(delete(SavedMap).where(SavedMap.world_id == world_id, SavedMap.key == key))
    return result.rowcount > 0
