"""Using an item outside a fight: a potion on a hurt hero, an ether, a revive.

The item's own ``use_effect`` says what it does; ``Rules.field_use`` turns it into new HP and MP (a game can allow more or
less). A hero resting between fights keeps its HP and MP in ``Hero.vitals`` (None means all full). Only the caller's own
heroes can be the user and the target (``terraforma.reach.helping`` lets a hero help one of another party), neither may be in a fight that is still running (the fight has its own item
command), and a use that would change nothing is refused, so the item isn't wasted. The roll comes from the world's
stream for this hero, item and stack size, so it repeats for the same state and a stack's uses differ.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..fights.content import ability_spec
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..fights.specs import EffectSpec
from ..models import Map, World
from ..world.rng import WorldRng
from .inventory import InventoryError, known_abilities, remove_item, stacks
from .models import Hero


class FieldUseError(InventoryError):
    """The item can't be used that way: the message says why."""


async def in_running_fight(session: AsyncSession, hero: Hero) -> bool:
    return await session.scalar(
        select(FightRecord.id).join(FightParticipant, FightParticipant.fight_id == FightRecord.id)
        .where(FightParticipant.hero_id == hero.id, FightRecord.finished.is_(False), FightParticipant.fled.is_(False)).limit(1)
    ) is not None


def vitals_of(hero: Hero, rules: Rules) -> tuple[dict[str, int], dict[str, int]]:
    maximums = {name: hero.stats[name] for name in rules.resource_names if name in hero.stats}
    return {name: min(max(0, (hero.vitals or {}).get(name, maximum)), maximum) for name, maximum in maximums.items()}, maximums


async def use_item(session: AsyncSession, user: Hero, position: int, target: Hero, rules: Rules, *, helping: bool = False) -> dict:
    """$user spends one of the item at $position on $target (the same hero, or another of the caller's; with $helping, a hero of
    another player, which the caller has already checked is in reach and not an enemy). Returns the target's resources
    before and after."""
    found = next(((stack, item) for stack, item in await stacks(session, user) if stack.position == position), None)
    if found is None:
        raise FieldUseError("there's nothing in that position")
    stack, item = found
    result = await apply_use(session, user, item, stack.qty, target, rules, helping=helping)
    if item.one_use:
        await remove_item(session, user, position, 1)
    await session.flush()
    return result


async def apply_use(session: AsyncSession, user: Hero, item: Item, stack_qty: int, target: Hero, rules: Rules, *, helping: bool = False) -> dict:
    """The effect of $user using $item (from a stack of $stack_qty) on $target: checks it can be done, changes the target's
    resources and returns them before and after. Taking the item from wherever it was is the caller's job (``used_up`` says
    whether it goes)."""
    if not item.use_effect:
        raise FieldUseError(f"{item.name} can't be used")
    if user.account_id != target.account_id and not helping:
        raise FieldUseError("you can only use it on your own heroes")
    for hero in {user.id: user, target.id: target}.values():
        if await in_running_fight(session, hero):
            raise FieldUseError(f"{hero.name} is in a fight")
    before, maximums = vitals_of(target, rules)
    world = await session.get(World, (await session.get(Map, target.map_id)).world_id)
    rng = WorldRng(world.seed).stream("field-use", user.id, target.id, item.key, stack_qty)
    after = rules.field_use(rng, EffectSpec.from_dict(item.use_effect), before, maximums)
    if after is None:
        raise FieldUseError(f"{item.name} can only be used in a fight")
    if after == before:
        raise FieldUseError(f"{item.name} would do nothing to {target.name}")
    target.vitals = None if after == maximums else after
    return {"hero_id": target.id, "before": before, "after": after, "used_up": item.one_use}


async def use_ability(session: AsyncSession, user: Hero, ability_key: str, target: Hero, rules: Rules, *, helping: bool = False) -> dict:
    """$user casts an ability it knows on $target outside a fight (the same hero, or another of the caller's; with $helping, a hero of another
    player, which the caller has already checked is in reach and not an enemy). What it does is ``Rules.field_use``, the rule items use; what it
    costs is ``Rules.ability_cost``, taken from the caster's resources. A cast that is refused, or would do nothing, costs nothing. The ability's
    wider targets are ignored: it always has the one target. Returns the target's resources before and after and what the cast cost."""
    ability = next((each for each in await known_abilities(session, user) if each.key == ability_key), None)
    if ability is None:
        raise FieldUseError(f"{user.name} doesn't know that")
    if user.account_id != target.account_id and not helping:
        raise FieldUseError("you can only cast it on your own heroes")
    for hero in {user.id: user, target.id: target}.values():
        if await in_running_fight(session, hero):
            raise FieldUseError(f"{hero.name} is in a fight")
    spec = ability_spec(ability)
    resource, amount = rules.ability_cost(spec)
    mine, mine_max = vitals_of(user, rules)
    if mine.get(rules.vital, 0) <= 0:
        raise FieldUseError(f"{user.name} has fallen")
    if mine.get(resource, 0) < amount:
        raise FieldUseError(f"{user.name} hasn't the {resource} for {ability.name}")
    paid = {**mine, resource: mine.get(resource, 0) - amount}  # (a caster who is also the target casts on itself after paying)
    before, maximums = (paid, mine_max) if target.id == user.id else vitals_of(target, rules)
    world = await session.get(World, (await session.get(Map, target.map_id)).world_id)
    rng = WorldRng(world.seed).stream("field-ability", user.id, target.id, ability.key, mine.get(resource, 0))
    after = rules.field_use(rng, spec.effect, before, maximums)
    if after is None:
        raise FieldUseError(f"{ability.name} can only be used in a fight")
    if after == before:
        raise FieldUseError(f"{ability.name} would do nothing to {target.name}")
    if target.id != user.id:
        user.vitals = None if paid == mine_max else paid
    target.vitals = None if after == maximums else after
    await session.flush()
    return {"hero_id": target.id, "before": before if target.id != user.id else mine, "after": after, "cost": {resource: amount}, "ability": ability.key}
