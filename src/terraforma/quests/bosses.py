"""Promoting existing monsters to bosses. The engine never makes up a monster: it takes ones the game has, multiplies their stats until
their strength (PXP, ``Rules.pxp``) matches what a band of teams needs, and lets the game add abilities. A boss is stored as a plain
entry, ``{"monster": key, "scale": 2.35, "pxp": 1990, "extras": {...}}``, that ``boss_combatant`` turns into a fighter."""

from random import Random
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Monster
from ..fights.combatant import Combatant
from ..fights.content import monster_combatant
from ..fights.rules import Rules
from .hooks import Band, Needs

#: The most a monster's stats are multiplied by, and the most a gang has.
MAX_SCALE = 60.0
MAX_GANG = 6
#: A monster is promoted only if it is at least this share of the strength it must reach (a rat does not become a dragon).
MIN_SHARE = 8


class BossError(ValueError):
    """Something the caller can fix: the message says what."""


def scaled(monster, factor: float, extra_abilities: list[str] | None = None) -> SimpleNamespace:
    """A copy of the monster row with every stat multiplied by $factor (at least 1 each) and the extra abilities added."""
    abilities = list(monster.abilities) + [key for key in (extra_abilities or []) if key not in monster.abilities]
    return SimpleNamespace(
        key=monster.key, name=monster.name, stats={name: max(1, round(value * factor)) for name, value in monster.stats.items()}, abilities=abilities,
        items=list(monster.items), equipment=list(monster.equipment), ai=dict(monster.ai), gold_reward=monster.gold_reward, drops=list(monster.drops or []))


async def tables(session: AsyncSession) -> tuple[dict, dict]:
    """The item and ability rows by key, which turning a monster into a fighter needs."""
    items = {row.key: row for row in (await session.scalars(select(Item))).all()}
    abilities = {row.key: row for row in (await session.scalars(select(Ability))).all()}
    return items, abilities


def strength(rules: Rules, monster, items: dict, abilities: dict, factor: float = 1.0, extra_abilities: list[str] | None = None) -> int:
    return rules.pxp(monster_combatant(scaled(monster, factor, extra_abilities), items, abilities, rules))


def scale_for(rules: Rules, monster, items: dict, abilities: dict, target: int, extra_abilities: list[str] | None = None) -> float | None:
    """The smallest factor (to three decimals) that brings the monster to $target strength; 1.0 if it is strong enough already; None if even
    ``MAX_SCALE`` is not enough."""
    if strength(rules, monster, items, abilities, 1.0, extra_abilities) >= target:
        return 1.0
    if strength(rules, monster, items, abilities, MAX_SCALE, extra_abilities) < target:
        return None
    low, high = 1.0, MAX_SCALE
    for _ in range(30):
        middle = (low + high) / 2
        if strength(rules, monster, items, abilities, middle, extra_abilities) >= target:
            high = middle
        else:
            low = middle
    return round(high + 0.0005, 3)  # (rounded up, so the boss is not a hair under)


def boss_combatant(monster, entry: dict, items: dict, abilities: dict, rules: Rules | None = None):
    """The fighter for one boss entry ($monster is the content row its ``monster`` key names)."""
    return monster_combatant(scaled(monster, entry["scale"], entry.get("extras", {}).get("abilities")), items, abilities, rules)


async def promote(session: AsyncSession, hooks: Needs, rules: Rules, band: Band, rng: Random) -> list[dict] | None:
    """The boss (or gang) for a band: monsters drawn from the ones the game has, each scaled so the whole gang adds up to the band's target
    strength. None when no monster the world has can be made to fit."""
    size = max(1, min(MAX_GANG, hooks.gang_size(rng, band)))
    each = hooks.target(band) // size
    if each < 1:
        return None
    items, abilities = await tables(session)
    rows = {row.key: row for row in (await session.scalars(select(Monster).where(Monster.active.is_(True)).order_by(Monster.id))).all()}
    base = {key: strength(rules, row, items, abilities) for key, row in rows.items()}
    candidates = [key for key, value in base.items() if each / MIN_SHARE <= value <= each]
    chosen = hooks.pick(rng, band, list(candidates))
    if chosen is None:
        if not candidates:
            return None
        chosen = rng.choices(candidates, weights=[max(1, base[key]) for key in candidates], k=size)
    elif not chosen or len(chosen) > MAX_GANG or any(key not in rows for key in chosen):
        raise BossError("the game picked monsters that do not exist, or none, or too many")
    bosses = []
    for key in chosen:
        extras = hooks.extras(rng, band, key)
        factor = scale_for(rules, rows[key], items, abilities, each, extras.get("abilities"))
        if factor is None:
            return None
        bosses.append({"monster": key, "scale": factor, "pxp": strength(rules, rows[key], items, abilities, factor, extras.get("abilities")), "extras": extras})
    return bosses


async def fighters(session: AsyncSession, rules: Rules, entries: list[dict]) -> list[Combatant]:
    """The fighters for a quest's boss entries (``spec["bosses"]``), ready to stand as the monsters' side of a fight. Raises BossError if a
    monster an entry names is no longer in the content."""
    items, abilities = await tables(session)
    rows = {row.key: row for row in (await session.scalars(select(Monster).where(Monster.key.in_({entry["monster"] for entry in entries})))).all()}
    made = []
    for entry in entries:
        if entry["monster"] not in rows:
            raise BossError(f"the boss {entry['monster']!r} is not a monster the world has any more")
        fighter = boss_combatant(rows[entry["monster"]], entry, items, abilities, rules)
        fighter.monster = entry["monster"]
        made.append(fighter)
    return made
