"""Monsters written by an admin instead of the seed.

The engine never makes a monster up: a monster is either in the game's seed or an admin wrote it here (an event, a coliseum bout, a boss
for a manual quest). An authored monster is an ordinary ``Monster`` row marked ``authored``, so loading the seed does not retire it, and it
is checked exactly as a seed row is (the same ``schema.Monster`` with the game's stats, then its references: its personality, abilities,
items and drop tables must exist and be active). It is never deleted, only retired, because fights and quests may still point at it.
Seed rows are changed in the seed, not here.
"""

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.rules import Rules
from ..quests.bosses import strength, tables
from . import models
from .schema import Monster

#: The columns an admin sets (the key is the row's name and never changes).
FIELDS = ("name", "personality", "xp_reward", "gold_reward", "stats", "abilities", "items", "equipment", "drops", "ai")


class AuthoringError(ValueError):
    """Something the admin can fix: the message says what."""


class NoSuchMonster(AuthoringError):
    pass


class Conflict(AuthoringError):
    pass


async def _check(session: AsyncSession, rules: Rules, key: str, data: dict) -> dict:
    """The row's columns from $data (as the seed would read them), or AuthoringError naming every problem."""
    try:
        row = Monster.model_validate({**data, "key": key}, context={"stats": rules.stats, "resources": rules.resource_names, "drop_scale": rules.drop_chance_scale})
    except ValidationError as error:
        raise AuthoringError("; ".join(f"{'.'.join(str(part) for part in each['loc'])}: {each['msg']}" for each in error.errors())) from error
    values = row.model_dump(mode="json")
    problems = []
    for field, table in (("personality", models.Personality), ("abilities", models.Ability), ("items", models.Item), ("equipment", models.Item),
                         ("drops", models.DropTable)):
        wanted = values[field] if isinstance(values[field], list) else [values[field]]
        found = set((await session.scalars(select(table.key).where(table.key.in_(wanted), table.active.is_(True)))).all()) if wanted else set()
        problems += [f"{field} names {name!r}, which the game does not have" for name in dict.fromkeys(wanted) if name not in found]
    if problems:
        raise AuthoringError("; ".join(problems))
    return {field: values[field] for field in FIELDS}


async def get(session: AsyncSession, key: str) -> models.Monster:
    row = await session.scalar(select(models.Monster).where(models.Monster.key == key))
    if row is None:
        raise NoSuchMonster(f"no such monster {key!r}")
    return row


async def _authored(session: AsyncSession, key: str) -> models.Monster:
    row = await get(session, key)
    if not row.authored:
        raise Conflict(f"monster {key!r} comes from the game's seed: change it there")
    return row


async def create(session: AsyncSession, rules: Rules, key: str, data: dict) -> models.Monster:
    """A new authored monster. The key must be unused (a retired monster's key too: restore or replace that one)."""
    values = await _check(session, rules, key, data)
    if await session.scalar(select(models.Monster.id).where(models.Monster.key == key)) is not None:
        raise Conflict(f"a monster with the key {key!r} exists already")
    row = models.Monster(key=key, active=True, authored=True, **values)
    session.add(row)
    await session.flush()
    return row


async def replace(session: AsyncSession, rules: Rules, key: str, data: dict) -> models.Monster:
    """Rewrites an authored monster whole (fights started from now on use it; those running keep what they were built with)."""
    row = await _authored(session, key)
    for field, value in (await _check(session, rules, key, data)).items():
        setattr(row, field, value)
    await session.flush()
    return row


async def set_active(session: AsyncSession, key: str, active: bool) -> models.Monster:
    """Retires an authored monster (no new encounter, boss or fight draws it) or brings it back."""
    row = await _authored(session, key)
    row.active = active
    await session.flush()
    return row


async def listing(session: AsyncSession, authored: bool | None, active: bool | None, after: int, limit: int) -> list[models.Monster]:
    """Monsters oldest first after the id $after; $authored and $active narrow the list when given."""
    query = select(models.Monster).where(models.Monster.id > after).order_by(models.Monster.id).limit(limit)
    if authored is not None:
        query = query.where(models.Monster.authored.is_(authored))
    if active is not None:
        query = query.where(models.Monster.active.is_(active))
    return list((await session.scalars(query)).all())


async def views(session: AsyncSession, rules: Rules, rows: list[models.Monster]) -> list[dict]:
    """What an admin reads about each monster, with its strength (``Rules.pxp``) so a bout can be balanced."""
    items, abilities = await tables(session)
    return [{"id": row.id, "key": row.key, "name": row.name, "personality": row.personality, "xp_reward": row.xp_reward, "gold_reward": row.gold_reward,
             "stats": row.stats, "abilities": row.abilities, "items": row.items, "equipment": row.equipment, "drops": row.drops or [], "ai": row.ai,
             "active": row.active, "authored": row.authored, "pxp": strength(rules, row, items, abilities)} for row in rows]
