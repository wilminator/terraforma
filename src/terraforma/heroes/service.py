"""Making, renaming and removing heroes and teams, and who is on which team.

Every call takes the account and only ever touches that account's own
heroes and teams. Limits are plain numbers for now (a game will be able to
set them later). They are checked by counting first, so two calls at the
same instant could each slip one past; names can't collide, the database
refuses that.
"""

import re

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Job
from ..models import Account, Map
from ..world.start import ensure_start
from ..fights.rules import Rules
from ..parties import service as parties
from . import inventory
from .models import Hero, HeroAbility, HeroEquipment, HeroItem, Team, TeamMember

MAX_HEROES = 12
MAX_TEAMS = 8
TEAM_SIZE = 4

NAME = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 _.'-]{0,22}[A-Za-z0-9])$")


class HeroError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(HeroError):
    pass


class NameTaken(HeroError):
    pass


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def check_name(name: str) -> str:
    name = name.strip()
    if not NAME.fullmatch(name) or "  " in name:
        raise HeroError("a name is 2-24 letters, digits, single spaces and _ . ' -, starting and ending with a letter or digit")
    return name


# --- heroes -------------------------------------------------------------------------

def starting_stats(job: Job) -> dict[str, int]:
    """What a new hero of $job starts with: one level of the job's growth."""
    return {stat: round(amount) for stat, amount in job.stat_growth.items()}


async def create_hero(session: AsyncSession, account: Account, name: str, job_key: str) -> Hero:
    name = check_name(name)
    job = await session.scalar(select(Job).where(Job.key == job_key, Job.active.is_(True)))
    if job is None:
        raise NotFound("there's no such job")
    if await session.scalar(select(func.count()).select_from(Hero).where(Hero.account_id == account.id)) >= MAX_HEROES:
        raise HeroError(f"an account can have {MAX_HEROES} heroes")
    hub = await ensure_start(session)
    hero = Hero(
        account_id=account.id, name=name, name_key=name_key(name), job_id=job.id,
        stats=starting_stats(job), map_id=hub.id, x=0, y=0,
    )
    session.add(hero)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as error:
        raise NameTaken("you already have a hero with that name") from error
    await inventory.grant_abilities(session, hero)
    return hero


def rest_hero(hero: Hero, rules: Rules | None = None) -> None:
    """Rests the hero: its resources (HP, MP, ...) become what the game's ``Rules.rest`` says (by default all full,
    and a dead hero lives again; a game can revive at 1 HP, at half, and so on). The engine itself never rests or
    regenerates a hero: a game calls this from its inn, potion, camp or level-up rule."""
    rules = rules or Rules()
    maximums = {name: hero.stats[name] for name in rules.resource_names if name in hero.stats}
    vitals = {name: min(max(0, (hero.vitals or {}).get(name, maximum)), maximum) for name, maximum in maximums.items()}
    rested = rules.rest(vitals, maximums)
    rested = {name: min(max(0, rested.get(name, vitals[name])), maximum) for name, maximum in maximums.items()}
    hero.vitals = None if rested == maximums else rested


async def own_hero(session: AsyncSession, account: Account, hero_id: int) -> Hero:
    hero = await session.scalar(select(Hero).where(Hero.id == hero_id, Hero.account_id == account.id))
    if hero is None:
        raise NotFound("there's no such hero")
    return hero


async def rename_hero(session: AsyncSession, account: Account, hero_id: int, name: str) -> Hero:
    hero = await own_hero(session, account, hero_id)
    name = check_name(name)
    hero.name, hero.name_key = name, name_key(name)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as error:
        raise NameTaken("you already have a hero with that name") from error
    return hero


async def delete_hero(session: AsyncSession, account: Account, hero_id: int) -> None:
    hero = await own_hero(session, account, hero_id)
    await session.execute(delete(TeamMember).where(TeamMember.hero_id == hero.id))
    for table in (HeroEquipment, HeroItem, HeroAbility):
        await session.execute(delete(table).where(table.hero_id == hero.id))
    await session.delete(hero)
    await session.flush()


async def list_heroes(session: AsyncSession, account: Account) -> list[dict]:
    rows = await session.execute(
        select(Hero, Job.key, Map.name)
        .join(Job, Job.id == Hero.job_id)
        .join(Map, Map.id == Hero.map_id)
        .where(Hero.account_id == account.id)
        .order_by(Hero.id)
    )
    return [hero_view(hero, job_key, map_name) for hero, job_key, map_name in rows.all()]


def hero_view(hero: Hero, job_key: str, map_name: str) -> dict:
    return {
        "id": hero.id, "name": hero.name, "job": job_key, "level": hero.level, "xp": hero.xp, "stats": hero.stats,
        "place": {"map": map_name, "x": hero.x, "y": hero.y},
    }


# --- teams --------------------------------------------------------------------------

async def create_team(session: AsyncSession, account: Account, name: str) -> Team:
    name = check_name(name)
    if await session.scalar(select(func.count()).select_from(Team).where(Team.account_id == account.id)) >= MAX_TEAMS:
        raise HeroError(f"an account can have {MAX_TEAMS} teams")
    team = Team(account_id=account.id, name=name, name_key=name_key(name))
    session.add(team)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as error:
        raise NameTaken("you already have a team with that name") from error
    return team


async def own_team(session: AsyncSession, account: Account, team_id: int) -> Team:
    team = await session.scalar(select(Team).where(Team.id == team_id, Team.account_id == account.id))
    if team is None:
        raise NotFound("there's no such team")
    return team


async def rename_team(session: AsyncSession, account: Account, team_id: int, name: str) -> Team:
    team = await own_team(session, account, team_id)
    name = check_name(name)
    team.name, team.name_key = name, name_key(name)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as error:
        raise NameTaken("you already have a team with that name") from error
    return team


async def delete_team(session: AsyncSession, account: Account, team_id: int) -> None:
    team = await own_team(session, account, team_id)
    await parties.leave_party(session, team.id)
    await session.execute(delete(TeamMember).where(TeamMember.team_id == team.id))
    await session.delete(team)
    await session.flush()


async def add_to_team(session: AsyncSession, account: Account, team_id: int, hero_id: int, party_size: int = Rules.party_size) -> TeamMember:
    """Puts the hero in the team's first free slot. A hero on another team must leave it first, and if the team is
    in a party the party needs a place for the hero ($party_size is the game's: ``Rules.party_size``)."""
    team = await own_team(session, account, team_id)
    hero = await own_hero(session, account, hero_id)
    if await session.scalar(select(TeamMember.id).where(TeamMember.hero_id == hero.id)):
        raise HeroError("that hero is already on a team: take them off it first")
    taken = set((await session.scalars(select(TeamMember.slot).where(TeamMember.team_id == team.id))).all())
    free = [slot for slot in range(TEAM_SIZE) if slot not in taken]
    if not free:
        raise HeroError(f"a team has room for {TEAM_SIZE} heroes")
    try:
        await parties.check_room(session, team.id, party_size)
    except parties.PartyError as error:
        raise HeroError(str(error)) from error
    member = TeamMember(team_id=team.id, hero_id=hero.id, slot=free[0])
    session.add(member)
    await session.flush()
    return member


async def remove_from_team(session: AsyncSession, account: Account, team_id: int, hero_id: int) -> None:
    team = await own_team(session, account, team_id)
    result = await session.execute(delete(TeamMember).where(TeamMember.team_id == team.id, TeamMember.hero_id == hero_id))
    if result.rowcount == 0:
        raise NotFound("that hero isn't on this team")


async def list_teams(session: AsyncSession, account: Account) -> list[dict]:
    teams = (await session.scalars(select(Team).where(Team.account_id == account.id).order_by(Team.id))).all()
    rows = await session.execute(
        select(TeamMember.team_id, TeamMember.slot, Hero.id, Hero.name)
        .join(Hero, Hero.id == TeamMember.hero_id)
        .where(TeamMember.team_id.in_([team.id for team in teams]))
        .order_by(TeamMember.team_id, TeamMember.slot)
    )
    members: dict[int, list[dict]] = {team.id: [] for team in teams}
    for team_id, slot, hero_id, hero_name in rows.all():
        members[team_id].append({"slot": slot, "hero_id": hero_id, "name": hero_name})
    return [{"id": team.id, "name": team.name, "members": members[team.id]} for team in teams]
