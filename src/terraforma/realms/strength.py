"""A realm's strength over time, and the falls and foundings it brings.

Like a supply, the strength is not stepped by a job: it is brought up to the world clock when it is looked at (``drift``), so every server
agrees. The game's ``Realms.target`` says where it is heading (from how full its settlements' supplies are); it moves toward that by at most
``Realms.step`` for each tick since it was last looked at. The target is read once, now, for the whole time that passed: a pass that runs
often (housekeeping) keeps that close to what really happened. ``judge`` then asks the game which settlements fall or are founded.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import World
from ..world.rng import WorldRng
from . import news, service, supply
from .hooks import Influence, Realms
from .models import DORMANT, LIVE, RUINS, Realm, Settlement

log = logging.getLogger(__name__)


async def fullness(session: AsyncSession, realm: Realm) -> list[int]:
    """How full each supply of each live settlement of the realm is, in whole percent of its capacity (a supply with no capacity counts as empty)."""
    found = []
    for town in await service.settlements(session, realm, LIVE):
        for _material, row in await supply.supplies(session, town):
            found.append(row.level * 100 // row.capacity if row.capacity > 0 else 0)
    return found


async def drift(session: AsyncSession, hooks: Realms, realm: Realm) -> Realm:
    """Brings the realm's strength up to the world clock: toward the game's target by ``hooks.step`` a tick, never past it."""
    now = (await session.get(World, realm.world_id)).tick
    elapsed = now - realm.as_of_tick
    if elapsed > 0:
        target = hooks.target(realm, await fullness(session, realm))
        if target is not None:
            move = min(abs(target - realm.strength), max(0, hooks.step) * elapsed)
            realm.strength += move if target > realm.strength else -move
        realm.as_of_tick = now
        await session.flush()
    return realm


async def influences(session: AsyncSession, hooks: Realms, settlement: Settlement) -> list[Influence]:
    """The realms with a live settlement in this one's sphere (the same map, within ``Settlement.sphere`` tiles, or the hooks' default), nearest first.
    A settlement with no place on its map has none."""
    if settlement.x is None or settlement.y is None:
        return []
    radius = settlement.sphere or hooks.sphere_default
    found: dict[int, list[tuple[int, Settlement]]] = {}
    for other in (await session.scalars(select(Settlement).where(Settlement.map_id == settlement.map_id, Settlement.state == LIVE, Settlement.id != settlement.id, Settlement.x.is_not(None)))).all():
        far = max(abs(other.x - settlement.x), abs(other.y - settlement.y))
        if far <= radius:
            found.setdefault(other.realm_id, []).append((far, other))
    out = []
    for realm_id, places in found.items():
        full = [each.level * 100 // each.capacity if each.capacity > 0 else 0 for _far, town in places for _material, each in await supply.supplies(session, town)]
        out.append(Influence(await session.get(Realm, realm_id), len(places), min(far for far, _town in places), sum(full) // len(full) if full else 0))
    return sorted(out, key=lambda each: (each.nearest, each.realm.key))


async def judge(session: AsyncSession, hooks: Realms) -> int:
    """For every realm: drifts its strength, lets the settlements the game says fall, and founds at most one that the game says to (dormant
    before ruins; one that can't be founded is skipped). Returns how many settlements fell or were founded."""
    changed = 0
    for realm in (await session.scalars(select(Realm).order_by(Realm.id))).all():
        before = realm.as_of_tick
        await drift(session, hooks, realm)
        await _raid(session, hooks, realm, realm.as_of_tick - before)
        for town in await service.settlements(session, realm, LIVE):
            if not hooks.governs(town) and hooks.falls(realm, town):
                await service.fall(session, town)
                changed += 1
        for state in (DORMANT, RUINS):
            if await _found_one(session, hooks, realm, state):
                changed += 1
                break
    return changed + await _judge_governed(session, hooks)


async def _raid(session: AsyncSession, hooks: Realms, realm: Realm, elapsed: int) -> int:
    """The raids the game says fell on the realm's live settlements in the $elapsed ticks since it was last looked at: each cuts a supply for a while and
    posts its line. Nothing happens when no time has passed. Returns how many."""
    if elapsed <= 0:
        return 0
    world = await session.get(World, realm.world_id)
    towns = await service.settlements(session, realm, LIVE)
    done = 0
    for raid in hooks.raids(realm, towns, WorldRng(world.seed).stream("raid", realm.key, world.tick), elapsed):
        try:
            await supply.set_cut(session, raid.settlement, raid.material, raid.cut, raid.ticks)
        except service.RealmError:  # (a raid on a supply the settlement does not keep is the game's mistake: skipped, not fatal)
            log.info("realm raid: %s keeps no %s", raid.settlement.name, raid.material)
            continue
        if raid.text:
            await news.post(session, realm.world_id, raid.text, "raid", raid.settlement)
        done += 1
    return done


async def _judge_governed(session: AsyncSession, hooks: Realms) -> int:
    """The settlements the game says live between realms (``Realms.governs``): each one's support is read once, from the realms around it, and a live
    one falls when it is under ``holds_at`` and a dormant one or one in ruins is founded when it is at or over ``founds_at``. Supply the realms keep is brought
    up to the clock as it is read."""
    changed = 0
    for town in (await session.scalars(select(Settlement).order_by(Settlement.id))).all():
        if not hooks.governs(town):
            continue
        support = hooks.support(town, await influences(session, hooks, town))
        if town.state == LIVE and support < hooks.holds_at:
            await service.fall(session, town)
            changed += 1
        elif town.state != LIVE and support >= hooks.founds_at:
            try:
                async with session.begin_nested():
                    await service.found(session, town)
            except service.RealmError:
                log.info("realm influence: %s can't be founded now", town.name)
                continue
            changed += 1
    return changed


async def _found_one(session: AsyncSession, hooks: Realms, realm: Realm, state: str) -> bool:
    for town in await service.settlements(session, realm, state):
        if hooks.governs(town) or not hooks.founds(realm, town):
            continue
        try:
            async with session.begin_nested():
                await service.found(session, town)
        except service.RealmError:  # (the world refuses it: it is skipped and the next one is tried)
            log.info("realm strength: %s can't be founded now", town.name)
            continue
        return True
    return False
