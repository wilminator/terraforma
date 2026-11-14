"""The world's bulletin: short lines about what happened to it (a settlement fell, a village was founded), posted by the engine and by the game, and
read by the dialog tag ``news`` on a bulletin board in a town. Only the latest ``KEEP`` lines of a world are kept."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import World
from .models import NewsPost, Settlement

#: How many posts a world keeps; older ones are dropped as new ones arrive.
KEEP = 50
#: The longest a post is (``NewsPost.text``).
MAX_TEXT = 255


async def post(session: AsyncSession, world_id: int, text: str, kind: str = "news", settlement: Settlement | None = None) -> NewsPost:
    """Posts a line to the world's bulletin, at the world clock's tick now (cut to ``MAX_TEXT``)."""
    row = NewsPost(world_id=world_id, settlement_id=settlement.id if settlement else None, kind=kind[:16], text=text[:MAX_TEXT], at_tick=(await session.get(World, world_id)).tick)
    session.add(row)
    await session.flush()
    old = (await session.scalars(select(NewsPost.id).where(NewsPost.world_id == world_id).order_by(NewsPost.id.desc()).offset(KEEP))).all()
    if old:
        await session.execute(delete(NewsPost).where(NewsPost.id.in_(list(old))))
    return row


async def latest(session: AsyncSession, world_id: int, count: int) -> list[NewsPost]:
    """The newest $count posts, newest first."""
    return list((await session.scalars(select(NewsPost).where(NewsPost.world_id == world_id).order_by(NewsPost.id.desc()).limit(max(0, count)))).all())
