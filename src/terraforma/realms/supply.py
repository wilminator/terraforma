"""What a settlement has of each material, as the world clock runs: sources fill it, use drains it, and events cut the sources.

The level is not stepped by a job. It is worked out from the elapsed ticks when it is read or changed, so it is the same on every server and
nothing has to run for it to move. All numbers are whole.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import World
from .models import Material, Settlement, SettlementSupply
from .service import RealmError


async def tick(session: AsyncSession, settlement: Settlement) -> int:
    """The world clock now."""
    return (await session.get(World, settlement.world_id)).tick


def level_at(row: SettlementSupply, now: int) -> int:
    """The level the supply has at tick $now (not before ``as_of_tick``), without changing the row."""
    elapsed = max(0, now - row.as_of_tick)
    gained = row.rate * (100 - row.cut) * elapsed // 100
    return max(0, min(row.capacity, row.level + gained - row.drain * elapsed))


async def settle(session: AsyncSession, settlement: Settlement, row: SettlementSupply) -> SettlementSupply:
    """Brings the row up to the world clock: its level is now, its ``as_of_tick`` the clock."""
    now = await tick(session, settlement)
    row.level, row.as_of_tick = level_at(row, now), max(row.as_of_tick, now)
    await session.flush()
    return row


async def supply_of(session: AsyncSession, settlement: Settlement, material_key: str) -> SettlementSupply:
    row = await session.scalar(
        select(SettlementSupply).join(Material, Material.id == SettlementSupply.material_id)
        .where(SettlementSupply.settlement_id == settlement.id, Material.key == material_key)
    )
    if row is None:
        raise RealmError(f"There is no such supply: {settlement.name} has no {material_key!r}.")
    return await settle(session, settlement, row)


async def supplies(session: AsyncSession, settlement: Settlement) -> list[tuple[Material, SettlementSupply]]:
    """The settlement's supplies, brought up to date, by material key."""
    found = (await session.execute(
        select(Material, SettlementSupply).join(SettlementSupply, SettlementSupply.material_id == Material.id)
        .where(SettlementSupply.settlement_id == settlement.id).order_by(Material.key)
    )).all()
    return [(material, await settle(session, settlement, row)) for material, row in found]


async def set_cut(session: AsyncSession, settlement: Settlement, material_key: str, cut: int) -> SettlementSupply:
    """An event cuts the settlement's own sources of a material by $cut percent (100: nothing arrives; 0: back to normal). The level up to
    now is kept as it was; the new cut counts from now."""
    if not 0 <= cut <= 100:
        raise RealmError("A cut is a percent from 0 to 100.")
    row = await supply_of(session, settlement, material_key)
    row.cut = cut
    await session.flush()
    return row


async def take(session: AsyncSession, settlement: Settlement, material_key: str, amount: int) -> int:
    """Uses up to $amount of a material (a crafter at work); returns how much there was to take."""
    if amount < 0:
        raise RealmError("An amount is not negative.")
    row = await supply_of(session, settlement, material_key)
    taken = min(row.level, amount)
    row.level -= taken
    await session.flush()
    return taken


async def add(session: AsyncSession, settlement: Settlement, material_key: str, amount: int) -> int:
    """Brings $amount of a material in (a delivery quest); returns how much fitted under the capacity."""
    if amount < 0:
        raise RealmError("An amount is not negative.")
    row = await supply_of(session, settlement, material_key)
    added = min(row.capacity - row.level, amount)
    row.level += added
    await session.flush()
    return added


def view(material: Material, row: SettlementSupply) -> dict:
    return {"material": material.key, "name": material.name, "level": row.level, "capacity": row.capacity, "rate": row.rate, "drain": row.drain, "cut": row.cut}
