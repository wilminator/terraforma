"""Loading a game's checked content into the database. Safe to run on every start."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
from . import models
from .schema import Strict, check_seed

# Parents before the rows that name them.
TABLES = {
    "abilities": models.Ability,
    "items": models.Item,
    "personalities": models.Personality,
    "jobs": models.Job,
    "monsters": models.Monster,
}


def _values(kind: str, row: Strict) -> dict:
    """The row's columns: its fields, with nested formats as plain JSON."""
    values = row.model_dump(mode="json")
    if kind == "personalities":
        values["animations"] = {
            name: values.pop(name)
            for name in ("base", "equip", "flee", "hit", "die", "attack_close", "attack_throw", "attack_shoot", "skill", "spell", "item")
        }
    return {**values, "active": True}


async def load_content(session: AsyncSession, seed: dict[str, list[dict]]) -> dict[str, int]:
    """Checks $seed (raising ContentError if it's wrong) and makes the database match it.

    New keys are added, known keys updated, and keys the seed no longer lists
    are marked inactive (never deleted). Returns how many rows of each kind
    the seed has. A seed with no content files leaves the database alone.
    """
    checked = check_seed(seed)
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
    # The rows were written with bulk SQL: content already loaded in this session is stale.
    session.expire_all()
    return counts
