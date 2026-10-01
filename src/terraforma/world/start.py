"""The place new heroes start: the engine's world and its hub map, made when missing.

Safe to call on every start and from several processes at once.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from ..models import Map, World

WORLD_NAME = "world"
HUB_NAME = "hub"


async def ensure_start(session: AsyncSession) -> Map:
    """The hub map, making the world and the hub first if this is a new database."""
    await upsert(session, World.__table__, {"name": WORLD_NAME}, ["name"])
    world = await session.scalar(select(World).where(World.name == WORLD_NAME))
    await upsert(session, Map.__table__, {"world_id": world.id, "name": HUB_NAME}, ["world_id", "name"])
    return await session.scalar(select(Map).where(Map.world_id == world.id, Map.name == HUB_NAME))
