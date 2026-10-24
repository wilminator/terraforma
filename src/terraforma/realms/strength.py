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
from . import service, supply
from .hooks import Realms
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


async def judge(session: AsyncSession, hooks: Realms) -> int:
    """For every realm: drifts its strength, lets the settlements the game says fall, and founds at most one that the game says to (dormant
    before ruins; one that can't be founded is skipped). Returns how many settlements fell or were founded."""
    changed = 0
    for realm in (await session.scalars(select(Realm).order_by(Realm.id))).all():
        await drift(session, hooks, realm)
        for town in await service.settlements(session, realm, LIVE):
            if hooks.falls(realm, town):
                await service.fall(session, town)
                changed += 1
        for state in (DORMANT, RUINS):
            if await _found_one(session, hooks, realm, state):
                changed += 1
                break
    return changed


async def _found_one(session: AsyncSession, hooks: Realms, realm: Realm, state: str) -> bool:
    for town in await service.settlements(session, realm, state):
        if not hooks.founds(realm, town):
            continue
        try:
            async with session.begin_nested():
                await service.found(session, town)
        except service.RealmError:  # (the world refuses it: it is skipped and the next one is tried)
            log.info("realm strength: %s can't be founded now", town.name)
            continue
        return True
    return False
