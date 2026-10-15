"""Making, renaming and removing heroes and teams, and who is on which team.

Every call takes the account and only ever touches that account's own
heroes and teams. The limits are the game's (``Rules.team_min``, ``team_max`` and ``max_teams``). They are checked by
counting first, so two calls at the same instant could each slip one past; names can't collide, the database
refuses that.

The calls the browser makes keep every hero on a team: a team is saved with its heroes (``save_team``), a hero is added to
one, and removing, replacing or moving a hero is the game's to allow (``Game.roster``). ``create_hero``, ``create_team``,
``add_to_team`` and ``delete_team`` are the building blocks under them, and what tests and seeds use.
"""

import re

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Job, Personality
from ..models import Account, Map
from ..npcs.models import NpcTalk
from ..parties.models import PartyRequest, PartyTeam
from ..fighttags.models import FightTeam, TeamKept, TeamTag
from ..profiles.models import TeamProfile
from ..guild.ranks import forget_team as forget_ranks
from ..quests.service import forget_team as forget_quests
from ..quests.rotation import forget_team as forget_slot_quests
from ..quests.weekly import forget_team as forget_weekly_quests
from ..storage.service import forget as forget_storage
from ..standing import service as standing
from ..world.start import ensure_start
from ..fights import pending
from ..fights.rules import Rules
from ..parties import service as parties
from ..alliances.hooks import Alliances
from ..alliances.service import remove_team
from ..relations.hooks import Ref
from ..relations.service import forget
from . import field, inventory
from .hooks import Roster, Start
from .models import Hero, HeroAbility, HeroEquipment, HeroItem, Team, TeamMember

#: The most heroes a team ever holds, whatever the game's ``Rules.team_max``: the engine's screens draw no more.
TEAM_LIMIT = 5

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


async def list_jobs(session: AsyncSession) -> list[dict]:
    """The jobs a new hero can take, in the game's order: what a hero of each starts with."""
    jobs = (await session.scalars(select(Job).where(Job.active.is_(True)).order_by(Job.id))).all()
    return [{"key": job.key, "name": job.name, "stats": starting_stats(job)} for job in jobs]


#: A tile of a map: ``(map_id, x, y)``.
Spot = tuple[int, int, int]


async def start_spot(session: AsyncSession, account: Account, start: Start, key: str | None) -> Spot | None:
    """Where a new team starts: the place of the game's ``Start.places`` the player picked by $key (the first, if they sent none),
    or None (the engine's hub) when the game offers none. Refused when $key is not one of the offered places, or when there is
    nothing to pick from."""
    places = await start.places(session, account)
    if not places:
        if key is not None:
            raise HeroError("there is no choice of where to start in this game")
        return None
    if key is None:
        place = places[0]
    else:
        place = next((each for each in places if each.key == key), None)
        if place is None:
            raise HeroError("that is not a place to start")
    hub = await ensure_start(session)
    row = await session.scalar(select(Map).where(Map.world_id == hub.world_id, Map.name == place.map))
    if row is None:
        raise HeroError("that place to start is not in the world yet")
    return row.id, place.x, place.y


async def team_spot(session: AsyncSession, team_id: int) -> Spot | None:
    """Where the team's first hero stands, or None for a team with no heroes."""
    hero = await session.scalar(select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot).limit(1))
    return None if hero is None else (hero.map_id, hero.x, hero.y)


async def create_hero(session: AsyncSession, account: Account, name: str, job_key: str, spot: Spot | None = None) -> Hero:
    """Makes a hero at $spot, or on the hub map when there is none."""
    name = check_name(name)
    job = await session.scalar(select(Job).where(Job.key == job_key, Job.active.is_(True)))
    if job is None:
        raise NotFound("there's no such job")
    map_id, x, y = spot if spot is not None else ((await ensure_start(session)).id, 0, 0)
    hero = Hero(
        account_id=account.id, name=name, name_key=name_key(name), job_id=job.id,
        stats=starting_stats(job), map_id=map_id, x=x, y=y,
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
    if await field.in_running_fight(session, hero):
        raise HeroError(f"{hero.name} is in a fight: delete them when it is over")
    await session.execute(delete(TeamMember).where(TeamMember.hero_id == hero.id))
    for table in (HeroEquipment, HeroItem, HeroAbility):
        await session.execute(delete(table).where(table.hero_id == hero.id))
    await session.execute(delete(NpcTalk).where(NpcTalk.hero_id == hero.id))
    await standing.forget(session, "hero", hero.id)
    await pending.forget_hero(session, hero)  # their drop answers, wins and place in finished fights
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

async def create_team(session: AsyncSession, account: Account, name: str, max_teams: int = Rules.max_teams) -> Team:
    name = check_name(name)
    if await session.scalar(select(func.count()).select_from(Team).where(Team.account_id == account.id)) >= max_teams:
        raise HeroError(f"you can have {max_teams} teams" if max_teams != 1 else "you can have 1 team")
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


async def delete_team(session: AsyncSession, account: Account, team_id: int, alliances: Alliances | None = None) -> None:
    """Deletes the team: it leaves its party and its alliances (the game's ``Alliances`` says how an alliance copes), and
    its relationships go."""
    team = await own_team(session, account, team_id)
    try:
        await parties.leave_party(session, team.id)
    except parties.PartyError as error:  # (a team in a town with its party: it leaves with the town's own call)
        raise HeroError(str(error)) from error
    await remove_team(session, alliances or Alliances(), team.id)
    await forget(session, Ref("team", team.id))
    await session.execute(delete(TeamMember).where(TeamMember.team_id == team.id))
    await session.execute(delete(TeamProfile).where(TeamProfile.team_id == team.id))
    for table in (TeamTag, TeamKept, FightTeam):  # its tags, its slots and its results in finished fights go with it
        await session.execute(delete(table).where(table.team_id == team.id))
    await forget_quests(session, team.id)
    await forget_ranks(session, team.id)
    await forget_weekly_quests(session, team.id)
    await forget_slot_quests(session, team.id)
    await forget_storage(session, "team", team.id)
    await session.execute(delete(PartyRequest).where(PartyRequest.team_id == team.id))
    await standing.forget(session, "team", team.id)
    await session.delete(team)
    await session.flush()


async def add_to_team(session: AsyncSession, account: Account, team_id: int, hero_id: int, party_size: int = Rules.party_size, most: int = Rules.team_max) -> TeamMember:
    """Puts the hero in the team's first free slot. A hero on another team must leave it first, and if the team is
    in a party the party needs a place for the hero ($party_size is the game's: ``Rules.party_size``; $most is its
    ``Rules.team_max``, never more than ``TEAM_LIMIT``)."""
    team = await own_team(session, account, team_id)
    hero = await own_hero(session, account, hero_id)
    if await session.scalar(select(TeamMember.id).where(TeamMember.hero_id == hero.id)):
        raise HeroError("that hero is already on a team: take them off it first")
    most = min(most, TEAM_LIMIT)
    taken = set((await session.scalars(select(TeamMember.slot).where(TeamMember.team_id == team.id))).all())
    free = [slot for slot in range(TEAM_LIMIT) if slot not in taken]
    if len(taken) >= most or not free:
        raise HeroError(f"a team has room for {most} heroes")
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
        select(TeamMember.team_id, TeamMember.slot, Hero.id, Hero.name, Personality.map_art)
        .join(Hero, Hero.id == TeamMember.hero_id)
        .join(Job, Job.id == Hero.job_id)
        .outerjoin(Personality, Personality.key == Job.key)  # (a hero has no personality of its own yet: its job's key names it)
        .where(TeamMember.team_id.in_([team.id for team in teams]))
        .order_by(TeamMember.team_id, TeamMember.slot)
    )
    members: dict[int, list[dict]] = {team.id: [] for team in teams}
    for team_id, slot, hero_id, hero_name, map_art in rows.all():
        # (a personality with no map art keeps a JSON null, which is not an SQL one)
        members[team_id].append({"slot": slot, "hero_id": hero_id, "name": hero_name, **({"map_art": map_art} if map_art else {})})
    in_party = dict((await session.execute(select(PartyTeam.team_id, PartyTeam.party_id).where(PartyTeam.team_id.in_([team.id for team in teams])))).all())
    return [{"id": team.id, "name": team.name, "members": members[team.id], "party": in_party.get(team.id)} for team in teams]


# --- teams with their heroes: what the browser's calls do ---------------------------------

def team_most(rules: Rules) -> int:
    """The most heroes a team holds: the game's ``team_max``, never more than ``TEAM_LIMIT``."""
    return max(0, min(rules.team_max, TEAM_LIMIT))


def team_rules(rules: Rules) -> dict:
    """What the game says about teams, for the screen that makes them."""
    return {"team_min": rules.team_min, "team_max": team_most(rules), "max_teams": rules.max_teams}


async def member_count(session: AsyncSession, team_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id)) or 0


async def _members(session: AsyncSession, team_id: int) -> list[TeamMember]:
    return list((await session.scalars(select(TeamMember).where(TeamMember.team_id == team_id).order_by(TeamMember.slot))).all())


async def _team_of_hero(session: AsyncSession, hero: Hero) -> TeamMember:
    member = await session.scalar(select(TeamMember).where(TeamMember.hero_id == hero.id))
    if member is None:
        raise NotFound("that hero isn't on a team")
    return member


async def save_team(session: AsyncSession, account: Account, name: str, heroes: list[tuple[str, str]], rules: Rules, spot: Spot | None = None) -> Team:
    """Makes a team, with heroes ($heroes: a name and a job key each, none at all for a team that is filled in on its own page), in one
    go: refused unless there are at most the game's ``team_max`` of them, and unless the player has room for another team. A team
    with fewer than ``team_min`` heroes is incomplete: it cannot play until it is filled. The heroes start at $spot (see ``start_spot``)."""
    most = team_most(rules)
    if len(heroes) > most:
        raise HeroError(f"a team has room for {most} heroes")
    team = await create_team(session, account, name, rules.max_teams)
    for slot, (hero_name, job_key) in enumerate(heroes):
        hero = await create_hero(session, account, hero_name, job_key, spot)
        session.add(TeamMember(team_id=team.id, hero_id=hero.id, slot=slot))
    await session.flush()
    return team


async def add_new_hero(session: AsyncSession, account: Account, team_id: int, name: str, job_key: str, rules: Rules, start: Start | None = None, start_key: str | None = None) -> Hero:
    """Makes a hero in the team's first free place: allowed while the team has room (``team_max``). The hero stands where the team
    does; the first hero of an empty team starts at the place the player picked from the game's ``Start`` ($start_key)."""
    team = await own_team(session, account, team_id)
    most = team_most(rules)
    taken = {member.slot for member in await _members(session, team.id)}
    if len(taken) >= most:
        raise HeroError(f"a team has room for {most} heroes")
    try:
        await parties.check_room(session, team.id, rules.party_size)
    except parties.PartyError as error:
        raise HeroError(str(error)) from error
    spot = await team_spot(session, team.id)
    if spot is None and start is not None:
        spot = await start_spot(session, account, start, start_key)
    elif start_key is not None:
        raise HeroError("this team has heroes already: a new hero starts where they stand")
    hero = await create_hero(session, account, name, job_key, spot)
    session.add(TeamMember(team_id=team.id, hero_id=hero.id, slot=min(set(range(TEAM_LIMIT)) - taken)))
    await session.flush()
    return hero


async def _on_team(session: AsyncSession, account: Account, team_id: int, hero_id: int) -> tuple[Team, Hero, TeamMember]:
    team = await own_team(session, account, team_id)
    hero = await own_hero(session, account, hero_id)
    member = await session.scalar(select(TeamMember).where(TeamMember.team_id == team.id, TeamMember.hero_id == hero.id))
    if member is None:
        raise NotFound("that hero isn't on this team")
    return team, hero, member


async def remove_hero(session: AsyncSession, account: Account, team_id: int, hero_id: int, rules: Rules, roster: Roster) -> None:
    """Removes the hero for good, if the game's ``Roster.may_remove`` allows it. The place stays empty for a new hero (the team
    cannot play while it has fewer than the game's ``team_min``)."""
    team, hero, _ = await _on_team(session, account, team_id, hero_id)
    if why := await roster.may_remove(session, account, hero):
        raise HeroError(why)
    await delete_hero(session, account, hero.id)


async def replace_hero(session: AsyncSession, account: Account, team_id: int, hero_id: int, name: str, job_key: str, roster: Roster) -> Hero:
    """Removes the hero and makes a new one in the same place, if the game's ``Roster.may_remove`` allows it."""
    _, hero, member = await _on_team(session, account, team_id, hero_id)
    check_name(name)
    if await session.scalar(select(Job.id).where(Job.key == job_key, Job.active.is_(True))) is None:
        raise NotFound("there's no such job")
    if why := await roster.may_remove(session, account, hero):
        raise HeroError(why)
    team_id, slot, spot = member.team_id, member.slot, (hero.map_id, hero.x, hero.y)
    await delete_hero(session, account, hero.id)
    new = await create_hero(session, account, name, job_key, spot)
    session.add(TeamMember(team_id=team_id, hero_id=new.id, slot=slot))
    await session.flush()
    return new


async def move_hero(session: AsyncSession, account: Account, hero_id: int, to_team_id: int, rules: Rules, roster: Roster) -> TeamMember:
    """Moves the hero to another of the player's teams, if that team has room and the game's ``Roster.may_move`` allows it."""
    hero = await own_hero(session, account, hero_id)
    to_team = await own_team(session, account, to_team_id)
    member = await _team_of_hero(session, hero)
    if member.team_id == to_team.id:
        raise HeroError("that hero is already on that team")
    if await field.in_running_fight(session, hero):
        raise HeroError(f"{hero.name} is in a fight: move them when it is over")
    there = await _members(session, to_team.id)
    if len(there) >= team_most(rules):
        raise HeroError(f"a team has room for {team_most(rules)} heroes")
    try:
        await parties.check_room(session, to_team.id, rules.party_size)
    except parties.PartyError as error:
        raise HeroError(str(error)) from error
    if why := await roster.may_move(session, account, hero, to_team):
        raise HeroError(why)
    member.team_id, member.slot = to_team.id, min(set(range(TEAM_LIMIT)) - {each.slot for each in there})
    await session.flush()
    return member


async def swap_heroes(session: AsyncSession, account: Account, hero_id: int, with_id: int, roster: Roster) -> None:
    """Exchanges the places of two heroes on two of the player's teams (each team keeps its number of heroes), if the game's
    ``Roster.may_move`` allows both moves."""
    if hero_id == with_id:
        raise HeroError("pick two different heroes")
    first, second = await own_hero(session, account, hero_id), await own_hero(session, account, with_id)
    one, two = await _team_of_hero(session, first), await _team_of_hero(session, second)
    if one.team_id == two.team_id:
        raise HeroError("those heroes are on the same team")
    for hero in (first, second):
        if await field.in_running_fight(session, hero):
            raise HeroError(f"{hero.name} is in a fight: swap them when it is over")
    team_one, team_two = await own_team(session, account, one.team_id), await own_team(session, account, two.team_id)
    for hero, to_team in ((first, team_two), (second, team_one)):
        if why := await roster.may_move(session, account, hero, to_team):
            raise HeroError(why)
    (team_a, slot_a), (team_b, slot_b) = (one.team_id, one.slot), (two.team_id, two.slot)
    one.slot = -1  # out of the way for a moment, so neither place is taken when the other hero moves in
    await session.flush()
    two.team_id, two.slot = team_a, slot_a
    await session.flush()
    one.team_id, one.slot = team_b, slot_b
    await session.flush()


async def disband_team(session: AsyncSession, account: Account, team_id: int, roster: Roster, alliances: Alliances | None = None) -> None:
    """Deletes the team and its heroes, if the game's ``Roster.may_disband`` allows it."""
    team = await own_team(session, account, team_id)
    if why := await roster.may_disband(session, account, team):
        raise HeroError(why)
    for member in await _members(session, team.id):
        await delete_hero(session, account, member.hero_id)
    await delete_team(session, account, team.id, alliances)
