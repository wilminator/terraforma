"""A hero's inventory, equipment and abilities, and gold.

The inventory is a short list of stacks, each at a position (0, 1, 2, ...
with no gaps). Equippable gear never stacks; ammunition and things that
aren't equipment stack up to MAX_ITEM_QTY. Equipment is kept by slot:
an item names the slots it takes (``equip_slots`` in the seed). Hand,
ammo and arm are *sided*: an item that takes "hand" goes in the left
hand (side 0) or the right (side 1); a two-handed weapon names both
"lhand" and "rhand". Equipping never moves anything out of the way: the
answer says what is in the way (NEEDS_UNEQUIPPING) and the player decides.

The limits are plain numbers for now; the fight rules framework will let
a game set them.
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Job
from ..fights.gear import AMMO_SLOTS, SIDED, EquipOutcome, EquipResult, equipment_bonus, find_slot, round_half_up
from .models import Hero, HeroAbility, HeroEquipment, HeroItem

MAX_ITEMS = 12
MAX_ITEM_QTY = 250

php_round = round_half_up  # the name the tests and callers already use

class InventoryError(ValueError):
    """Something the player can fix: the message says what."""


def stackable(item: Item) -> bool:
    return not item.equip_slots or any(slot in AMMO_SLOTS for slot in item.equip_slots)


def ammo_type(item: Item) -> str:
    return (item.attack or {}).get("ammo_type", "")


# --- loading -----------------------------------------------------------------------

async def stacks(session: AsyncSession, hero: Hero) -> list[tuple[HeroItem, Item]]:
    rows = await session.execute(
        select(HeroItem, Item).join(Item, Item.id == HeroItem.item_id)
        .where(HeroItem.hero_id == hero.id).order_by(HeroItem.position, HeroItem.id)
    )
    return [(stack, item) for stack, item in rows.all()]


async def equipment(session: AsyncSession, hero: Hero) -> dict[str, int]:
    """Slot name -> the id of the stack in it."""
    rows = await session.execute(select(HeroEquipment.slot, HeroEquipment.hero_item_id).where(HeroEquipment.hero_id == hero.id))
    return {slot: stack_id for slot, stack_id in rows.all()}


async def worn_items(session: AsyncSession, hero: Hero) -> list[Item]:
    """The distinct items being worn or wielded (a two-handed weapon counts once)."""
    worn = set((await equipment(session, hero)).values())
    return [item for stack, item in await stacks(session, hero) if stack.id in worn]


async def _renumber(session: AsyncSession, hero: Hero) -> None:
    for position, (stack, _item) in enumerate(await stacks(session, hero)):
        stack.position = position
    await session.flush()


# --- the inventory -----------------------------------------------------------------------

async def add_item(session: AsyncSession, hero: Hero, item_key: str, qty: int) -> int:
    """Puts $qty of an item in the inventory. Returns how many didn't fit (0 when all did)."""
    if qty < 1:
        raise InventoryError("add at least one")
    item = await session.scalar(select(Item).where(Item.key == item_key))
    if item is None:
        raise InventoryError(f"there's no item {item_key!r}")
    current = await stacks(session, hero)
    if stackable(item):
        for stack, other in current:
            if other.id == item.id and stack.qty < MAX_ITEM_QTY and qty:
                moved = min(qty, MAX_ITEM_QTY - stack.qty)
                stack.qty += moved
                qty -= moved
    count = len(current)
    while qty and count < MAX_ITEMS:
        size = min(qty, MAX_ITEM_QTY) if stackable(item) else 1
        session.add(HeroItem(hero_id=hero.id, item_id=item.id, position=count, qty=size))
        qty -= size
        count += 1
    await session.flush()
    return qty


async def remove_item(session: AsyncSession, hero: Hero, position: int, qty: int) -> int:
    """Takes up to $qty from the stack at $position (the whole stack goes, and comes off, if that's all of it). Returns how many went."""
    for stack, _item in await stacks(session, hero):
        if stack.position == position:
            if stack.qty > qty:
                stack.qty -= qty
                await session.flush()
                return qty
            removed = stack.qty
            await session.execute(delete(HeroEquipment).where(HeroEquipment.hero_item_id == stack.id))
            await session.delete(stack)
            await session.flush()
            await _renumber(session, hero)
            return removed
    return 0


async def move_item(session: AsyncSession, hero: Hero, from_position: int, to_position: int) -> None:
    """Moves a stack to a new position; the ones between slide over by one."""
    current = [stack for stack, _item in await stacks(session, hero)]
    if not (0 <= from_position < len(current) and 0 <= to_position < len(current)):
        raise InventoryError("there's nothing in that position")
    current.insert(to_position, current.pop(from_position))
    for position, stack in enumerate(current):
        stack.position = position
    await session.flush()


# --- equipment ---------------------------------------------------------------------------------

async def equip(session: AsyncSession, hero: Hero, position: int, side: int = 0) -> EquipResult:
    """Wields or wears the stack at $position, on $side (0 left, 1 right) for the sided slots."""
    current = await stacks(session, hero)
    found = next(((stack, item) for stack, item in current if stack.position == position), None)
    if found is None:
        return EquipResult(EquipOutcome.NOT_FOUND)
    stack, item = found
    if not item.equip_slots:
        return EquipResult(EquipOutcome.NOT_EQUIPABLE)
    worn = await equipment(session, hero)
    by_id = {other.id: other.position for other, _ in current}
    slots = list(item.equip_slots)
    # Every slot it needs must be empty.
    for slot in slots:
        occupant = worn.get(find_slot(slot, side))
        if occupant is not None:
            return EquipResult(EquipOutcome.NEEDS_UNEQUIPPING, occupying_position=by_id[occupant])
    # Already worn somewhere else: take it off first.
    for slot, occupant in worn.items():
        if occupant == stack.id and slot not in slots:
            return EquipResult(EquipOutcome.NEEDS_UNEQUIPPING, occupying_position=position)
    # Ammunition by itself needs a weapon that takes it.
    if slots == ["ammo"]:
        weapon_id = worn.get(find_slot("hand", side))
        weapon = next((other for other_stack, other in current if other_stack.id == weapon_id), None)
        if weapon is None or ammo_type(item) != ammo_type(weapon):
            return EquipResult(EquipOutcome.INCOMPATIBLE_AMMO)
        if "lhand" in weapon.equip_slots and "rhand" in weapon.equip_slots:
            side = 0  # a two-handed weapon's ammunition goes on the left
    placed = []
    for slot in slots:
        name = find_slot(slot, side)
        session.add(HeroEquipment(hero_id=hero.id, slot=name, hero_item_id=stack.id))
        placed.append(name)
    await session.flush()
    return EquipResult(EquipOutcome.SUCCESS, slots=tuple(placed))


async def unequip(session: AsyncSession, hero: Hero, position: int) -> EquipResult:
    """Takes the stack at $position off. A weapon's ammunition comes off with it."""
    current = await stacks(session, hero)
    found = next(((stack, item) for stack, item in current if stack.position == position), None)
    if found is None:
        return EquipResult(EquipOutcome.NOT_FOUND)
    stack, item = found
    if not item.equip_slots:
        return EquipResult(EquipOutcome.NOT_EQUIPABLE)
    worn = await equipment(session, hero)
    freed = [slot for slot, occupant in worn.items() if occupant == stack.id]
    side = ""
    for slot in freed:
        if slot in ("lhand", "rhand"):
            side = slot[0] if side == "" else "l"
    if side and ammo_type(item) and not any(slot in AMMO_SLOTS for slot in item.equip_slots):
        if f"{side}ammo" in worn:
            freed.append(f"{side}ammo")
    await session.execute(delete(HeroEquipment).where(HeroEquipment.hero_id == hero.id, HeroEquipment.slot.in_(freed)))
    await session.flush()
    return EquipResult(EquipOutcome.SUCCESS, slots=tuple(freed))


# --- abilities -----------------------------------------------------------------------------------

async def grant_abilities(session: AsyncSession, hero: Hero) -> list[str]:
    """Teaches the hero every active ability its job grants at or below its level. Returns the names newly learned."""
    job = await session.get(Job, hero.job_id)
    wanted = {entry["ability"]: entry["level"] for entry in job.abilities}
    if not wanted:
        return []
    abilities = (await session.scalars(select(Ability).where(Ability.key.in_(wanted), Ability.active.is_(True)))).all()
    known = set((await session.scalars(select(HeroAbility.ability_id).where(HeroAbility.hero_id == hero.id))).all())
    learned = []
    for ability in sorted(abilities, key=lambda ability: ability.name):
        if hero.level >= wanted[ability.key] and ability.id not in known:
            session.add(HeroAbility(hero_id=hero.id, ability_id=ability.id))
            learned.append(ability.name)
    await session.flush()
    return learned


async def known_abilities(session: AsyncSession, hero: Hero) -> list[Ability]:
    rows = await session.scalars(
        select(Ability).join(HeroAbility, HeroAbility.ability_id == Ability.id)
        .where(HeroAbility.hero_id == hero.id).order_by(Ability.name)
    )
    return list(rows.all())


# --- what the player sees ---------------------------------------------------------------------------

async def view(session: AsyncSession, hero: Hero) -> dict:
    current = await stacks(session, hero)
    worn = await equipment(session, hero)
    slots_of: dict[int, list[str]] = {}
    for slot, stack_id in worn.items():
        slots_of.setdefault(stack_id, []).append(slot)
    position_of = {stack.id: stack.position for stack, _item in current}
    return {
        "gold": hero.gold,
        "items": [
            {"position": stack.position, "item": item.key, "name": item.name, "qty": stack.qty,
             "equipped_in": sorted(slots_of.get(stack.id, []))}
            for stack, item in current
        ],
        "equipment": {slot: position_of[stack_id] for slot, stack_id in sorted(worn.items())},
        "abilities": [{"key": ability.key, "name": ability.name} for ability in await known_abilities(session, hero)],
    }
