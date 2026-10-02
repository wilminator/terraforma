"""Heroes and monsters from the database, as fighters. (The only fight code that reads the database.)

A hero starts a fight at full HP and MP for now: heroes do not carry damage between fights yet.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, DropTable, Item, Job, Monster, Status
from ..heroes import inventory
from ..heroes.models import Hero, Team, TeamMember
from ..parties import service as parties
from .combatant import Combatant
from .content import ability_spec, drop_table_spec, item_spec, monster_combatant, status_spec
from .rules import Rules
from .drops import DropTable as DropTableSpec
from .status import StatusSpec


async def hero_fighter(session: AsyncSession, hero: Hero) -> Combatant:
    """The hero as it goes into a fight: its stats (resources at the level the last fight left them), what it knows,
    its inventory and what it wears."""
    stacks = await inventory.stacks(session, hero)
    position_of = {stack.id: stack.position for stack, _item in stacks}
    worn = await inventory.equipment(session, hero)
    job = await session.get(Job, hero.job_id)
    current = dict(hero.stats)
    for name, value in (hero.vitals or {}).items():  # what the last fight left (never above the maximum)
        if name in current:
            current[name] = max(0, min(value, current[name]))
    return Combatant(
        name=hero.name, base=dict(hero.stats), current=current,
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


async def known_statuses(session: AsyncSession) -> dict[str, StatusSpec]:
    """Every active status in the content, by key: what a new fight is built with (``build_fight(layout, statuses)``)."""
    rows = await session.scalars(select(Status).where(Status.active.is_(True)))
    return {row.key: status_spec(row) for row in rows.all()}


async def party_side(session: AsyncSession, party_id: int, rules: Rules) -> dict[int, list[Combatant]]:
    """A party's heroes as one side of a fight: groups of ``Rules.group_size``, filled team by team in the order the
    teams joined the party (``{group: [combatants...]}``, ready for ``build_fight({side: ...})``). A team can span groups:
    a party keeps its teams whole, a fight's groups are only where they stand."""
    heroes = [await session.get(Hero, hero_id) for hero_id in await parties.hero_ids(session, party_id)]
    fighters = [await hero_fighter(session, hero) for hero in heroes]
    return {number: fighters[start:start + rules.group_size] for number, start in enumerate(range(0, len(fighters), rules.group_size))}


async def known_drop_tables(session: AsyncSession, keys: set[str]) -> dict[str, DropTableSpec]:
    """The active drop tables named in $keys, by key, with the items they drop: what a new fight is built with."""
    tables = (await session.scalars(select(DropTable).where(DropTable.key.in_(keys), DropTable.active.is_(True)))).all() if keys else []
    wanted = {entry["item"] for table in tables for entry in table.entries if entry["item"] is not None}
    items = {item.key: item for item in (await session.scalars(select(Item).where(Item.key.in_(wanted)))).all()} if wanted else {}
    return {table.key: drop_table_spec(table, items) for table in tables}
