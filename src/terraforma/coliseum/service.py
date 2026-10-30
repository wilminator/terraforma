"""Coliseum bouts: a team's owner challenges another team, the other owner answers, and an accepted bout is a fight at once.

There is no reach, party, town or PvP-zone rule: the arena is the place. Both teams need heroes, none of them may be in a running fight or
barred by a forced disuse, and two teams of one account may not fight each other (it would farm PvP tags and invite thrown fights). The fight
stands where the challenger's first hero stands, and is public once it finishes (``fighttags``). An offer ends after
``Rules.coliseum_offer_seconds`` of wall clock; ``expire`` closes the late ones.
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..disuse import gate as disuse
from ..fights import store, timing
from ..fights.build import known_statuses, team_party
from ..fights.fight import build_fight
from ..fights.live import _groups
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..heroes import service as heroes
from ..heroes.models import Hero, Team, TeamMember
from ..models import Account, Map
from .models import DECLINED, EXPIRED, FOUGHT, OFFERED, WITHDRAWN, Bout


class ColiseumError(Exception):
    """The bout can't be made or answered: the message says why."""


def view(bout: Bout) -> dict:
    return {"id": bout.id, "challenger": bout.challenger_id, "challenged": bout.challenged_id, "state": bout.state,
            "expires_at": bout.expires_at, "fight_id": bout.fight_id}


async def challenge(session: AsyncSession, rules: Rules, account: Account, team_id: int, other_team_id: int) -> Bout:
    """The caller's team asks another team to a bout. Refused, with the reason, for a team that is not the caller's, one with no heroes,
    one of the caller's own, or one that is already asked by (or asking) this team."""
    mine = await heroes.own_team(session, account, team_id)
    theirs = await session.get(Team, other_team_id)
    if theirs is None:
        raise ColiseumError("there's no such team")
    if theirs.account_id == account.id:
        raise ColiseumError("you can't fight your own team")
    for team in (mine, theirs):
        if not await _members(session, team):
            raise ColiseumError(f"{team.name} has no heroes")
    if await session.scalar(select(Bout.id).where(Bout.state == OFFERED, Bout.challenger_id == mine.id, Bout.challenged_id == theirs.id).limit(1)):
        raise ColiseumError("that team has already been asked")
    bout = Bout(challenger_id=mine.id, challenged_id=theirs.id, state=OFFERED, expires_at=wallclock.timestamp() + rules.coliseum_offer_seconds)
    session.add(bout)
    await session.flush()
    return bout


async def _members(session: AsyncSession, team: Team) -> list[Hero]:
    rows = await session.execute(select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team.id).order_by(TeamMember.slot))
    return list(rows.scalars().all())


async def _offered(session: AsyncSession, bout_id: int) -> Bout:
    bout = await session.get(Bout, bout_id)
    if bout is None:
        raise ColiseumError("there's no such bout")
    if bout.state != OFFERED:
        raise ColiseumError(f"that bout is {bout.state}")
    if bout.expires_at <= wallclock.timestamp():
        bout.state = EXPIRED
        await session.flush()
        raise ColiseumError("that bout has expired")
    return bout


async def _answering(session: AsyncSession, account: Account, bout: Bout, team_id: int) -> None:
    """The caller's team $team_id is the one the bout asks."""
    await heroes.own_team(session, account, team_id)
    if bout.challenged_id != team_id:
        raise ColiseumError("there's no such bout")


async def _asking(session: AsyncSession, account: Account, bout: Bout, team_id: int) -> None:
    """The caller's team $team_id is the one that made the offer."""
    await heroes.own_team(session, account, team_id)
    if bout.challenger_id != team_id:
        raise ColiseumError("there's no such bout")


async def decline(session: AsyncSession, account: Account, bout_id: int, team_id: int) -> Bout:
    bout = await _offered(session, bout_id)
    await _answering(session, account, bout, team_id)
    bout.state = DECLINED
    await session.flush()
    return bout


async def withdraw(session: AsyncSession, account: Account, bout_id: int, team_id: int) -> Bout:
    bout = await _offered(session, bout_id)
    await _asking(session, account, bout, team_id)
    bout.state = WITHDRAWN
    await session.flush()
    return bout


async def accept(session: AsyncSession, rules: Rules, account: Account, bout_id: int, team_id: int) -> Bout:
    """The challenged team's owner takes the bout: the fight starts now (challenger is party 0, the challenged team party 1; every player
    commands their own heroes). Nothing changes if a team is not ready (a hero in a fight, or barred)."""
    bout = await _offered(session, bout_id)
    await _answering(session, account, bout, team_id)
    sides, side_teams, hero_ids = [], [], []
    for team_id in (bout.challenger_id, bout.challenged_id):
        team = await session.get(Team, team_id)
        fighters, ids = await team_party(session, team, rules)
        if not fighters:
            raise ColiseumError(f"{team.name} has no heroes")
        sides.append(fighters)
        side_teams.append(ids)
        hero_ids += [hero for each in ids.values() for hero in each]
    if await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_(hero_ids), FightRecord.finished.is_(False), FightParticipant.fled.is_(False)).limit(1)
    ) is not None:
        raise ColiseumError("a hero of one of those teams is already in a fight")
    if reason := await disuse.blocked(session, hero_ids):
        raise ColiseumError(reason)
    accounts = (await session.scalars(select(Hero.account_id).where(Hero.id.in_(hero_ids)).distinct())).all()
    multiplier = timing.fight_multiplier([await timing.multiplier_for(session, owner) for owner in accounts])
    fight = build_fight({number: _groups(fighters, rules.group_size) for number, fighters in enumerate(sides)}, await known_statuses(session), {}, [])
    for number, teams in enumerate(side_teams):
        fight.parties[number].teams = teams
    first = await session.get(Hero, side_teams[0][bout.challenger_id][0])
    game_map = await session.get(Map, first.map_id)
    fight.can_flee = game_map.zone(first.x, first.y)["can_flee"]
    record = await store.create_fight(session, game_map, fight, first.x, first.y)
    record.time_multiplier = multiplier
    record.round_deadline = wallclock.now() + timedelta(seconds=rules.round_length(multiplier))
    bout.state, bout.fight_id = FOUGHT, record.id
    await session.flush()
    return bout


async def bouts_of(session: AsyncSession, account: Account, team_id: int) -> list[Bout]:
    """The team's bouts (asked or asking), newest first."""
    team = await heroes.own_team(session, account, team_id)
    query = select(Bout).where((Bout.challenger_id == team.id) | (Bout.challenged_id == team.id)).order_by(Bout.id.desc()).limit(50)
    return list((await session.scalars(query)).all())


async def expire(session: AsyncSession) -> int:
    """Closes the offers that ran out. Returns how many."""
    late = (await session.scalars(select(Bout).where(Bout.state == OFFERED, Bout.expires_at <= wallclock.timestamp()))).all()
    for bout in late:
        bout.state = EXPIRED
    await session.flush()
    return len(late)
