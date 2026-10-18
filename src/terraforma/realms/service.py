"""Founding settlements, letting them fall and founding them again. The rules for *when* are the world's (later slices); this
is what happens to the records when it does."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Map
from ..towns.models import VisitedTown
from .models import DORMANT, LIVE, RUINS, Realm, Settlement


class RealmError(Exception):
    """Something the caller can fix: the message says what."""


async def create_realm(session: AsyncSession, world_id: int, key: str, name: str, strength: int = 0) -> Realm:
    if await session.scalar(select(Realm.id).where(Realm.world_id == world_id, Realm.key == key)) is not None:
        raise RealmError(f"There is already a realm {key!r}.")
    realm = Realm(world_id=world_id, key=key, name=name, strength=strength)
    session.add(realm)
    await session.flush()
    return realm


async def add_settlement(session: AsyncSession, realm: Realm, map_id: int, key: str, name: str, state: str = LIVE, tier: int = 0) -> Settlement:
    """A settlement of the realm on the map: live now, or dormant until it is founded."""
    if state not in (LIVE, DORMANT):
        raise RealmError("A settlement starts live or dormant.")
    if tier < 0:
        raise RealmError("A settlement's tier is not negative.")
    if await session.get(Map, map_id) is None:
        raise RealmError("That map does not exist.")
    if await session.scalar(select(Settlement.id).where(Settlement.world_id == realm.world_id, Settlement.key == key)) is not None:
        raise RealmError(f"There is already a settlement {key!r}.")
    settlement = Settlement(world_id=realm.world_id, realm_id=realm.id, map_id=map_id, key=key, name=name, state=state, tier=tier)
    session.add(settlement)
    await session.flush()
    return settlement


async def found(session: AsyncSession, settlement: Settlement) -> Settlement:
    """A dormant settlement is founded, or one in ruins is founded again: it is live."""
    if settlement.state == LIVE:
        raise RealmError(f"{settlement.name} is already live.")
    settlement.state = LIVE
    await session.flush()
    return settlement


async def fall(session: AsyncSession, settlement: Settlement) -> Settlement:
    """A live settlement falls: it is kept as ruins and leaves the visited list of every team (unless another live settlement stands on
    its map, which keeps the map a town). Teams that go there again once it is founded again add it as new."""
    if settlement.state != LIVE:
        raise RealmError(f"{settlement.name} is not live.")
    settlement.state = RUINS
    await session.flush()
    if not await live_on(session, settlement.map_id):
        await session.execute(delete(VisitedTown).where(VisitedTown.map_id == settlement.map_id))
    return settlement


async def live_on(session: AsyncSession, map_id: int) -> bool:
    """Whether a live settlement stands on the map."""
    return await session.scalar(select(Settlement.id).where(Settlement.map_id == map_id, Settlement.state == LIVE).limit(1)) is not None


async def set_strength(session: AsyncSession, realm: Realm, strength: int) -> Realm:
    realm.strength = strength
    await session.flush()
    return realm


async def settlements(session: AsyncSession, realm: Realm, state: str | None = None) -> list[Settlement]:
    query = select(Settlement).where(Settlement.realm_id == realm.id).order_by(Settlement.key)
    if state is not None:
        query = query.where(Settlement.state == state)
    return list((await session.scalars(query)).all())
