"""Putting items in storage, taking them out, using them from there, and delivering new ones.

Storage is a short list of stacks like a pack (``heroes.inventory``): positions with no gaps, equipment never stacks, the
rest stack up to ``MAX_ITEM_QTY``; how many stacks it holds is the game's (``Storage.capacity``). Nothing moves for a hero in
a running fight, for an item that is worn, or where the game's rules say no. A move is all or nothing in what leaves the source
and what arrives: what does not fit stays where it was.
"""

from dataclasses import dataclass, field

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..fights.rules import Rules
from ..heroes import field as field_use
from ..heroes import inventory
from ..heroes.models import Hero
from .hooks import Owner, Storage
from .models import StorageItem


class StorageError(ValueError):
    """Something the player can fix: the message says what."""


@dataclass
class Delivery:
    """Where a delivery went: how many into storage, how many into each hero's pack (by hero id), and how many fitted nowhere."""

    stored: int = 0
    packed: dict[int, int] = field(default_factory=dict)
    left: int = 0


async def stacks(session: AsyncSession, owner: Owner) -> list[tuple[StorageItem, Item]]:
    rows = await session.execute(
        select(StorageItem, Item).join(Item, Item.id == StorageItem.item_id)
        .where(StorageItem.owner_kind == owner.kind, StorageItem.owner_id == owner.id).order_by(StorageItem.position, StorageItem.id)
    )
    return [(stack, item) for stack, item in rows.all()]


async def _owner_of(session: AsyncSession, storage: Storage, hero: Hero) -> Owner:
    """The hero's storage, after the rules that apply to every use of it."""
    owner = await storage.owner(session, hero)
    if owner is None:
        raise StorageError("there is no storage for this hero")
    if why := await storage.reachable(session, hero):
        raise StorageError(why)
    return owner


async def _outside_fight(session: AsyncSession, hero: Hero) -> None:
    if await field_use.in_running_fight(session, hero):
        raise StorageError(f"{hero.name} is in a fight")


async def _renumber(session: AsyncSession, owner: Owner) -> None:
    for position, (stack, _item) in enumerate(await stacks(session, owner)):
        stack.position = position
    await session.flush()


async def add(session: AsyncSession, storage: Storage, owner: Owner, item: Item, qty: int) -> int:
    """Puts $qty of $item in storage. Returns how many did not fit."""
    current = await stacks(session, owner)
    if inventory.stackable(item):
        for stack, other in current:
            if other.id == item.id and stack.qty < inventory.MAX_ITEM_QTY and qty:
                moved = min(qty, inventory.MAX_ITEM_QTY - stack.qty)
                stack.qty += moved
                qty -= moved
    count = len(current)
    while qty and count < storage.capacity:
        size = min(qty, inventory.MAX_ITEM_QTY) if inventory.stackable(item) else 1
        session.add(StorageItem(owner_kind=owner.kind, owner_id=owner.id, item_id=item.id, position=count, qty=size))
        qty -= size
        count += 1
    await session.flush()
    return qty


async def remove(session: AsyncSession, owner: Owner, position: int, qty: int) -> int:
    """Takes up to $qty from the stack at $position. Returns how many went."""
    for stack, _item in await stacks(session, owner):
        if stack.position == position:
            if stack.qty > qty:
                stack.qty -= qty
                await session.flush()
                return qty
            removed = stack.qty
            await session.delete(stack)
            await session.flush()
            await _renumber(session, owner)
            return removed
    return 0


async def forget(session: AsyncSession, kind: str, owner_id: int) -> None:
    """Drops an owner's storage (a deleted team's), items and all."""
    await session.execute(delete(StorageItem).where(StorageItem.owner_kind == kind, StorageItem.owner_id == owner_id))


async def view(session: AsyncSession, storage: Storage, hero: Hero) -> dict:
    """What the player sees of the hero's storage: whether there is any, and what is in it."""
    owner = await storage.owner(session, hero)
    if owner is None:
        return {"enabled": False, "owner": None, "capacity": 0, "items": []}
    return {
        "enabled": True, "owner": owner.kind, "capacity": storage.capacity,
        "items": [{"position": stack.position, "item": item.key, "name": item.name, "qty": stack.qty} for stack, item in await stacks(session, owner)],
    }


async def put(session: AsyncSession, storage: Storage, hero: Hero, position: int, qty: int) -> None:
    """Moves up to $qty from the stack at $position of the hero's pack into storage. All of it must fit."""
    await _outside_fight(session, hero)
    owner = await _owner_of(session, storage, hero)
    found = next(((stack, item) for stack, item in await inventory.stacks(session, hero) if stack.position == position), None)
    if found is None:
        raise StorageError("there's nothing in that position")
    stack, item = found
    if not 1 <= qty <= stack.qty:
        raise StorageError(f"you have {stack.qty} of that, and moving it is at least 1")
    if stack.id in (await inventory.equipment(session, hero)).values():
        raise StorageError("take it off first")
    if why := await storage.may_put(session, hero, item):
        raise StorageError(why)
    async with session.begin_nested():  # what does not all fit leaves storage as it was
        if await add(session, storage, owner, item, qty):
            raise StorageError("storage has no room for that")
    if await inventory.remove_item(session, hero, position, qty) != qty:
        raise StorageError("that is no longer there")


async def take(session: AsyncSession, storage: Storage, hero: Hero, position: int, qty: int) -> None:
    """Moves up to $qty from the stack at $position of storage into the hero's pack. All of it must fit."""
    await _outside_fight(session, hero)
    owner = await _owner_of(session, storage, hero)
    found = next(((stack, item) for stack, item in await stacks(session, owner) if stack.position == position), None)
    if found is None:
        raise StorageError("there's nothing in that position")
    stack, item = found
    if not 1 <= qty <= stack.qty:
        raise StorageError(f"storage has {stack.qty} of that, and moving it is at least 1")
    if why := await storage.may_take(session, hero, item):
        raise StorageError(why)
    async with session.begin_nested():  # what does not all fit leaves the pack as it was
        if await inventory.add_item(session, hero, item.key, qty):
            raise StorageError("your pack has no room for that")
    if await remove(session, owner, position, qty) != qty:
        raise StorageError("that is no longer there")


async def use(session: AsyncSession, storage: Storage, rules: Rules, hero: Hero, position: int, target: Hero) -> dict:
    """The hero uses one of the item at $position of storage on $target (one of the caller's heroes), outside a fight, if the
    game's ``Storage.may_use`` allows it. Same answer as ``heroes.field.use_item``."""
    await _outside_fight(session, hero)
    owner = await _owner_of(session, storage, hero)
    found = next(((stack, item) for stack, item in await stacks(session, owner) if stack.position == position), None)
    if found is None:
        raise StorageError("there's nothing in that position")
    stack, item = found
    if why := await storage.may_use(session, hero, item):
        raise StorageError(why)
    result = await field_use.apply_use(session, hero, item, stack.qty, target, rules)
    if item.one_use:
        await remove(session, owner, position, 1)
    await session.flush()
    return result


async def deliver(session: AsyncSession, storage: Storage, hero: Hero, item_key: str, qty: int) -> Delivery:
    """Delivers $qty of an item to the hero's side (a store's purchase, a reward): into storage first, then
    the packs of ``Storage.sharers`` in order, the next with room after each is full. Whatever fits nowhere is ``left``;
    nothing is raised for a full house, so the caller decides what to do with it."""
    item = await session.scalar(select(Item).where(Item.key == item_key))
    if item is None:
        raise StorageError(f"there's no item {item_key!r}")
    if qty < 1:
        raise StorageError("deliver at least one")
    delivery = Delivery()
    owner = await storage.owner(session, hero)
    if owner is not None:
        rest = await add(session, storage, owner, item, qty)
        delivery.stored, qty = qty - rest, rest
    for other in await storage.sharers(session, hero):
        if not qty:
            break
        fitted = qty - (rest := await inventory.add_item(session, other, item_key, qty))
        if fitted:
            delivery.packed[other.id] = fitted
        qty = rest
    delivery.left = qty
    return delivery

