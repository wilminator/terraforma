"""Founding settlements, letting them fall and founding them again. The rules for *when* are the world's (later slices); this
is what happens to the records when it does."""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.dialect import upsert
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


async def apply_realms(session: AsyncSession, world_id: int, realms: list, towns: list) -> int:
    """Makes the world's realms and settlements match the seed rows ($realms: ``RealmSeed``, $towns: ``SettlementSeed``): new ones are added,
    known ones (by key) get their names, realm, map and tier rewritten. What the world has moved since stays: a realm's strength and a
    settlement's state are only the seed's when the row is new. A row the seed no longer lists is left alone. Returns how many rows."""
    for row in realms:
        await upsert(session, Realm.__table__, {"world_id": world_id, "key": row.key, "name": row.name, "strength": row.strength}, ["world_id", "key"], insert_only=["strength"])
    await session.flush()
    realm_ids = {each.key: each.id for each in (await session.scalars(select(Realm).where(Realm.world_id == world_id))).all()}
    map_ids = {each.name: each.id for each in (await session.scalars(select(Map).where(Map.world_id == world_id))).all()}
    for row in towns:
        values = {"world_id": world_id, "key": row.key, "name": row.name, "realm_id": realm_ids[row.realm], "map_id": map_ids[row.map], "tier": row.tier, "state": row.state}
        await upsert(session, Settlement.__table__, values, ["world_id", "key"], insert_only=["state"])
    return len(realms) + len(towns)


async def realm(session: AsyncSession, realm_id: int) -> Realm:
    found = await session.get(Realm, realm_id)
    if found is None:
        raise RealmError("There is no such realm.")
    return found


async def settlement(session: AsyncSession, settlement_id: int) -> Settlement:
    found = await session.get(Settlement, settlement_id)
    if found is None:
        raise RealmError("There is no such settlement.")
    return found


def realm_view(row: Realm) -> dict:
    return {"id": row.id, "key": row.key, "name": row.name, "strength": row.strength}


def settlement_view(row: Settlement) -> dict:
    return {"id": row.id, "key": row.key, "name": row.name, "realm_id": row.realm_id, "map_id": row.map_id, "state": row.state, "tier": row.tier}
