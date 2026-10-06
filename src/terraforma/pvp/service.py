"""Starting a fight against another party: the place's flag, then the rules' range window."""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..fights import joining, store, timing
from ..fights.build import known_statuses, team_party
from ..fights.fight import build_fight
from ..fights.live import _groups
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..heroes import service as heroes
from ..heroes.models import Hero, Team
from ..models import Account, Map
from ..parties import service as parties
from ..towns import service as towns
from .hooks import PvpZones


def party_pxp(rules: Rules, fighters) -> int:
    """A party's strength: the sum of its fighters' potential experience."""
    return sum(rules.pxp(fighter) for fighter in fighters)


async def refusal(session: AsyncSession, zones: PvpZones, rules: Rules, attacker, target, map_id: int, x: int, y: int) -> str | None:
    """Why the party of fighters $attacker may not pick a fight with $target at this place, or None if it may."""
    allowed = await zones.allows_pvp(session, map_id, x, y)
    return rules.may_start_pvp(party_pxp(rules, attacker), party_pxp(rules, target), allowed)


# --- picking a fight ------------------------------------------------------------------------------------------------

class PvpError(ValueError):
    """Why a party may not pick that fight: the message says what."""


async def start_pvp_fight(session: AsyncSession, zones: PvpZones, rules: Rules, account: Account, team_id: int, target_party_id: int) -> FightRecord:
    """The party of $account's team $team_id picks a fight with the party $target_party_id (attacker is party 0, the target party 1; every
    player of either side commands their own heroes). Refused, with the reason, unless: the team is the account's and in a party;
    the target is another party that stands at the same spot; neither party is in a town or in a fight; and the place permits
    PvP and the target is inside the range window (``PvpZones.allows_pvp``, ``Rules.may_start_pvp``). Raises PvpError, or the
    heroes' NotFound for a team that is not the account's."""
    team = await heroes.own_team(session, account, team_id)
    mine = await parties.party_of(session, team.id)
    if mine is None:
        raise PvpError("a team must be in a party to pick a fight")
    if target_party_id == mine.id:
        raise PvpError("a party can't fight itself")
    try:
        theirs = await parties.get_party(session, target_party_id)
    except parties.NotFound as error:
        raise PvpError(str(error)) from error
    for party in (mine, theirs):
        if await towns.visit_of_party(session, party.id) is not None:
            raise PvpError("a party that is in a town can't fight")
        if await joining.offer_of(session, party.id) is not None:
            raise PvpError("a party the hub has offered a fight to join must answer it first")
    if (mine.map_id, mine.x, mine.y) != (theirs.map_id, theirs.x, theirs.y):
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
        .where(FightParticipant.hero_id.in_(hero_ids), FightRecord.finished.is_(False)).limit(1)
    ) is not None:
        raise PvpError("a hero of one of those parties is already in a fight")
    if reason := await refusal(session, zones, rules, sides[0], sides[1], mine.map_id, mine.x, mine.y):
        raise PvpError(reason)
    accounts = (await session.scalars(select(Hero.account_id).where(Hero.id.in_(hero_ids)).distinct())).all()
    multiplier = timing.fight_multiplier([await timing.multiplier_for(session, owner) for owner in accounts])
    fight = build_fight({number: _groups(fighters, rules.group_size) for number, fighters in enumerate(sides)}, await known_statuses(session), {}, [])
    for number, teams in enumerate(side_teams):
        fight.parties[number].teams = teams
    record = await store.create_fight(session, await session.get(Map, mine.map_id), fight, mine.x, mine.y)
    record.time_multiplier = multiplier
    record.round_deadline = wallclock.now() + timedelta(seconds=rules.round_length(multiplier))
    await session.flush()
    return record
