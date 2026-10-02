"""Heroes and monsters from the database, as fighters. (The only fight code that reads the database.)

A hero starts a fight at full HP and MP for now: heroes do not carry damage between fights yet.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Job, Monster
from ..heroes import inventory
from ..heroes.models import Hero, Team, TeamMember
from .combatant import Combatant
from .content import ability_spec, item_spec, monster_combatant
from .rules import Rules


async def hero_fighter(session: AsyncSession, hero: Hero) -> Combatant:
    """The hero as it goes into a fight: its stats, what it knows, its inventory and what it wears."""
    stacks = await inventory.stacks(session, hero)
    position_of = {stack.id: stack.position for stack, _item in stacks}
    worn = await inventory.equipment(session, hero)
    job = await session.get(Job, hero.job_id)
    return Combatant(
        name=hero.name, base=dict(hero.stats), current=dict(hero.stats),
        abilities=[ability_spec(ability) for ability in await inventory.known_abilities(session, hero)],
        inventory=[[item_spec(item), stack.qty] for stack, item in stacks],
        equipment={slot: position_of[stack_id] for slot, stack_id in worn.items()},
        charid=hero.id, level=hero.level, exp=hero.xp, job_need=job.xp_needed, growth=dict(job.stat_growth),
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


async def team_party(session: AsyncSession, team: Team) -> tuple[list[Combatant], dict[int, list[int]]]:
    """A team's heroes as fighters, in the order of their slots, and the ``{team id: [hero ids]}`` to set on the
    party's ``teams`` so the experience tree pays them (only fighters on a team earn)."""
    rows = await session.execute(
        select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team.id).order_by(TeamMember.slot)
    )
    heroes = list(rows.scalars().all())
    return [await hero_fighter(session, hero) for hero in heroes], {team.id: [hero.id for hero in heroes]}
