"""Loading a game's checked content into the database. Safe to run on every start."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from ..maps.service import apply_maps
from ..world.start import ensure_start
from . import models
from .schema import DROP_SCALE, RESOURCES, STATS, Strict, check_seed

# Parents before the rows that name them.
TABLES = {
    "statuses": models.Status,
    "abilities": models.Ability,
    "items": models.Item,
    "drop_tables": models.DropTable,
    "personalities": models.Personality,
    "jobs": models.Job,
    "monsters": models.Monster,
}


def _values(kind: str, row: Strict) -> dict:
    """The row's columns: its fields, with nested formats as plain JSON."""
    values = row.model_dump(mode="json", by_alias=True)
    if kind == "personalities":
        values["animations"] = {
            name: values.pop(name)
            for name in ("base", "equip", "flee", "hit", "die", "attack_close", "attack_throw", "attack_shoot", "skill", "spell", "item")
        }
    return {**values, "active": True}


async def load_content(session: AsyncSession, seed: dict[str, list[dict]], stats: tuple[str, ...] | None = None,
                       resources: tuple[str, ...] | None = None, drop_scale: int | None = None) -> dict[str, int]:
    """Checks $seed (raising ContentError if it's wrong) and makes the database match it.

    New keys are added, known keys updated, and keys the seed no longer lists
    are marked inactive (never deleted). Returns how many rows of each kind
    the seed has. A seed with no content files leaves the database alone.
    """
    checked = check_seed(seed, stats or STATS, resources or RESOURCES, drop_scale or DROP_SCALE)
    counts = {}
    for kind, table in TABLES.items():
        if kind not in seed:
            continue
        rows = checked[kind]
        for row in rows:
            await upsert(session, table.__table__, _values(kind, row), ["key"])
        await session.execute(
            update(table).where(table.key.not_in([row.key for row in rows])).values(active=False)
        )
        counts[kind] = len(rows)
    if "maps" in seed:
        # Maps belong to a world: the engine's, made first if this is a new database.
        hub = await ensure_start(session)
        counts["maps"] = await apply_maps(session, hub.world_id, checked["maps"])
    # The rows were written with bulk SQL: content already loaded in this session is stale.
    session.expire_all()
    return counts
