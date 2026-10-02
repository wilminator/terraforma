"""Heroes and monsters from the database, as fighters. (The only fight code that reads the database.)

A hero starts a fight at full HP and MP for now: heroes do not carry damage between fights yet.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Monster, Status
from ..heroes import inventory
from ..heroes.models import Hero
from .combatant import Combatant
from .content import ability_spec, item_spec, monster_combatant, status_spec
from .rules import Rules
from .status import StatusSpec


async def hero_fighter(session: AsyncSession, hero: Hero) -> Combatant:
    """The hero as it goes into a fight: its stats, what it knows, its inventory and what it wears."""
    stacks = await inventory.stacks(session, hero)
    position_of = {stack.id: stack.position for stack, _item in stacks}
    worn = await inventory.equipment(session, hero)
    return Combatant(
        name=hero.name, base=dict(hero.stats), current=dict(hero.stats),
        abilities=[ability_spec(ability) for ability in await inventory.known_abilities(session, hero)],
        inventory=[[item_spec(item), stack.qty] for stack, item in stacks],
        equipment={slot: position_of[stack_id] for slot, stack_id in worn.items()},
        charid=hero.id,
    )


async def monster_fighter(session: AsyncSession, key: str, rules: Rules | None = None) -> Combatant:
    """The monster with seed key $key as it goes into a fight."""
    monster = await session.scalar(select(Monster).where(Monster.key == key))
    if monster is None:
        raise LookupError(f"there is no monster {key!r}")
    items = {item.key: item for item in (await session.scalars(select(Item).where(Item.key.in_({*monster.items, *monster.equipment})))).all()}
    abilities = {ability.key: ability for ability in (await session.scalars(select(Ability).where(Ability.key.in_(monster.abilities)))).all()}
    fighter = monster_combatant(monster, items, abilities, rules)
    fighter.monster = key
    return fighter


async def known_statuses(session: AsyncSession) -> dict[str, StatusSpec]:
    """Every active status in the content, by key: what a new fight is built with (``build_fight(layout, statuses)``)."""
    rows = await session.scalars(select(Status).where(Status.active.is_(True)))
    return {row.key: status_spec(row) for row in rows.all()}
