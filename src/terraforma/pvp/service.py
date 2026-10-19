"""Starting a fight against another party: the game's reach rule, the place's flag, then the rules' range window."""

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..disuse import gate as disuse
from ..fights import joining, store, timing
from ..fights.build import known_statuses, team_party
from ..fights.fight import build_fight
from ..fights.live import _groups
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..heroes import service as heroes
from ..instances import service as instances
from ..heroes.models import Hero, Team
from ..models import Account, Map
from ..parties import service as parties
from ..parties.models import Party
from ..reach.hooks import FIGHT, Reach
from ..towns import service as towns
from .hooks import PvpZones


def party_pxp(rules: Rules, fighters) -> int:
    """A party's strength: the sum of its fighters' potential experience."""
    return sum(rules.pxp(fighter) for fighter in fighters)


async def refusal(session: AsyncSession, zones: PvpZones, rules: Rules, attacker, target, map_id: int, x: int, y: int, shared_instance: bool = False) -> str | None:
    """Why the party of fighters $attacker may not pick a fight with $target at this place, or None if it may. Two parties of
    one shared quest instance ($shared_instance) skip the range window: ``Rules.may_fight_in_shared_instance`` decides."""
    allowed = await zones.allows_pvp(session, map_id, x, y)
    if shared_instance:
        return rules.may_fight_in_shared_instance(allowed)
    return rules.may_start_pvp(party_pxp(rules, attacker), party_pxp(rules, target), allowed)


# --- picking a fight ------------------------------------------------------------------------------------------------

class PvpError(ValueError):
    """Why a party may not pick that fight: the message says what."""


@dataclass
class Matchup:
    """Two parties that may fight each other: the fighters of each side, the teams (hero ids by team) and every hero involved."""

    sides: list
    side_teams: list
    hero_ids: list[int]


async def check_fight(
    session: AsyncSession, zones: PvpZones, rules: Rules, reach: Reach | None, mine: Party, theirs: Party
) -> Matchup:
    """Whether the party $mine may pick a fight with $theirs now, changing nothing: the matchup if so, otherwise PvpError with the reason.
    The target must be another party in reach for a fight (``Reach.party`` with ``fight``: by default within one tile, on its map) and
    in the same instance; neither party may be in a town or in a fight; and the place must permit PvP (the attacker's tile and the
    target's) with the target inside the range window (``PvpZones.allows_pvp``, ``Rules.may_start_pvp``)."""
    if theirs.id == mine.id:
        raise PvpError("a party can't fight itself")
    for party in (mine, theirs):
        if await towns.visit_of_party(session, party.id) is not None:
            raise PvpError("a party that is in a town can't fight")
        if await joining.offer_of(session, party.id) is not None:
            raise PvpError("a party the hub has offered a fight to join must answer it first")
    if reason := await (reach or Reach()).party(session, FIGHT, mine, theirs):
        raise PvpError(reason)
    if not await instances.same_place(session, mine.id, theirs.id):
        raise PvpError("that party is not here")
    sides, hero_ids, side_teams = [], [], []
    for party in (mine, theirs):
        fighters, teams = [], {}
        for each in await parties.team_ids(session, party.id):
            members, ids = await team_party(session, await session.get(Team, each), rules)
            fighters += members
            teams |= ids
        sides.append(fighters)
        side_teams.append(teams)
        hero_ids += [hero for ids in teams.values() for hero in ids]
    if not all(sides):
        raise PvpError("a party with no heroes can't fight")
    if await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_(hero_ids), FightRecord.finished.is_(False), FightParticipant.fled.is_(False)).limit(1)
    ) is not None:
        raise PvpError("a hero of one of those parties is already in a fight")
    if reason := await disuse.blocked(session, hero_ids):
        raise PvpError(reason)
    shared = await instances.in_same_shared_instance(session, mine.id, theirs.id)
    for map_id, x, y in dict.fromkeys([(mine.map_id, mine.x, mine.y), (theirs.map_id, theirs.x, theirs.y)]):  # (a fight from range needs both tiles to allow it)
        if reason := await refusal(session, zones, rules, sides[0], sides[1], map_id, x, y, shared):
            raise PvpError(reason)
    return Matchup(sides, side_teams, hero_ids)


async def start_pvp_fight(
    session: AsyncSession, zones: PvpZones, rules: Rules, account: Account, team_id: int, target_party_id: int, reach: Reach | None = None
) -> FightRecord:
    """The party of $account's team $team_id picks a fight with the party $target_party_id (attacker is party 0, the target party 1; every
    player of either side commands their own heroes). Refused, with the reason, unless the team is the account's and in a party and the
    parties pass ``check_fight``. Raises PvpError, or the heroes' NotFound for a team that is not the account's."""
    team = await heroes.own_team(session, account, team_id)
    mine = await parties.party_of(session, team.id)
    if mine is None:
        raise PvpError("a team must be in a party to pick a fight")
    try:
        theirs = await parties.get_party(session, target_party_id)
    except parties.NotFound as error:
        raise PvpError(str(error)) from error
    matchup = await check_fight(session, zones, rules, reach, mine, theirs)
    sides, side_teams, hero_ids = matchup.sides, matchup.side_teams, matchup.hero_ids
    accounts = (await session.scalars(select(Hero.account_id).where(Hero.id.in_(hero_ids)).distinct())).all()
    multiplier = timing.fight_multiplier([await timing.multiplier_for(session, owner) for owner in accounts])
    fight = build_fight({number: _groups(fighters, rules.group_size) for number, fighters in enumerate(sides)}, await known_statuses(session), {}, [])
    for number, teams in enumerate(side_teams):
        fight.parties[number].teams = teams
    game_map = await session.get(Map, mine.map_id)
    fight.can_flee = game_map.zone(mine.x, mine.y)["can_flee"]
    record = await store.create_fight(session, game_map, fight, mine.x, mine.y)
    record.time_multiplier = multiplier
    record.round_deadline = wallclock.now() + timedelta(seconds=rules.round_length(multiplier))
    await session.flush()
    return record
