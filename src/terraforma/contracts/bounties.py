"""Bounties: a reward for killing a team, or one hero of it, that anyone may earn without accepting a contract.

A team (or an alliance, through one of its member teams, by the same rules as spending its treasury) posts a bounty at a guild: the target
team, whether the whole team or one named hero must fall, a reward in gold and how long it stands. The reward is held from the posting. It
is paid once: when the target has fallen (a ``Died`` event) in fights that began after the posting, ``judge`` pays it, split evenly, to the
teams whose heroes fought on the other side of those fights. If nobody does before it runs out, or the poster takes it back, the reward
goes back. A fall with no hero on the other side (the target died to monsters) earns nothing. Everything settles once.
"""

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..alliances import service as alliances
from ..alliances import treasury
from ..alliances.errors import AllianceError
from ..alliances.hooks import Alliances
from ..alliances.models import Alliance
from ..alliances.service import NeedsVote
from ..economy import Economy
from ..escrow import service as escrow
from ..fights.events import EventType
from ..fights.models import FightActionRecord, FightParticipant, FightRecord
from ..heroes.models import Hero, Team, TeamMember
from ..storage.hooks import Storage
from .models import BOUNTY_EXPIRED, BOUNTY_OPEN, BOUNTY_PAID, BOUNTY_WITHDRAWN, Bounty

PURPOSE = "bounty"
MAX_AMOUNT = 10**9
MAX_LIFETIME = 365 * 24 * 3600
SCOPES = ("team", "hero")


class BountyError(ValueError):
    """Something the caller can fix: the message says what."""


async def _team_of(session: AsyncSession, hero: Hero) -> int:
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        raise BountyError("only a hero on a team can post a bounty")
    return team_id


async def post(
    session: AsyncSession, economy: Economy, hero: Hero, guild: str, target_team_id: int, scope: str, hero_id: int | None, reward_gold: int,
    lifetime: int, alliance_hooks: Alliances | None = None, alliance_id: int | None = None,
) -> Bounty:
    """$hero's team posts a bounty at $guild on team $target_team_id (`scope` ``team``, or ``hero`` with $hero_id on that team) for
    $reward_gold, standing $lifetime seconds. With $alliance_id the gold comes from that alliance's treasury (the team must be allowed to
    spend it, and not need a vote); otherwise from the hero's purse. Raises BountyError, changing nothing, for anything wrong or short."""
    if not guild:
        raise BountyError("a bounty needs a guild")
    team_id = await _team_of(session, hero)
    if team_id == target_team_id:
        raise BountyError("a team cannot post a bounty on itself")
    if await session.get(Team, target_team_id) is None:
        raise BountyError("there is no such team to target")
    if scope not in SCOPES:
        raise BountyError("a bounty is for the whole team or one hero of it")
    if scope == "hero" and await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id)) != target_team_id:
        raise BountyError("that hero is not on the target team")
    if not 1 <= reward_gold <= MAX_AMOUNT or not 1 <= lifetime <= MAX_LIFETIME:
        raise BountyError("a bounty pays 1 gold or more and stands from a second to a year")
    bounty = Bounty(
        guild=guild, poster_team_id=team_id, alliance_id=alliance_id, target_team_id=target_team_id, scope=scope,
        hero_id=hero_id if scope == "hero" else None, reward_gold=reward_gold, posted_at=wallclock.timestamp(), expires_at=wallclock.timestamp() + lifetime,
    )
    async with session.begin_nested():
        session.add(bounty)
        await session.flush()
        if alliance_id is not None:
            try:
                alliance = await alliances.get_alliance(session, alliance_id)
                await alliances.commit_gold(session, alliance_hooks or Alliances(), alliance, team_id, reward_gold)
            except NeedsVote as error:
                raise BountyError("the alliance's members must vote before its treasury is spent") from error
            except AllianceError as error:
                raise BountyError(str(error)) from error
        else:
            try:
                deposit = await escrow.hold(session, economy, hero, guild, PURPOSE, str(bounty.id), gold=reward_gold)
            except escrow.EscrowError as error:
                raise BountyError(str(error)) from error
            bounty.escrow_id = deposit.id
        await session.flush()
    return bounty


async def _give_back(session: AsyncSession, economy: Economy, storage: Storage, bounty: Bounty) -> None:
    """The reward goes back to where it came from (the alliance's treasury, or the poster's team if the alliance is gone)."""
    if bounty.escrow_id is not None:
        await escrow.refund(session, economy, storage, bounty.escrow_id)
    elif bounty.alliance_id is not None and (alliance := await session.get(Alliance, bounty.alliance_id)) is not None:
        await treasury.put(session, alliance, bounty.reward_gold)
    else:
        await economy.credit_team(session, bounty.poster_team_id, bounty.reward_gold)


async def _close(session: AsyncSession, economy: Economy, storage: Storage, bounty_id: int, state: str) -> Bounty:
    bounty = await session.get(Bounty, bounty_id)
    if bounty is None:
        raise BountyError("there is no such bounty")
    async with session.begin_nested():
        claimed = await session.execute(
            update(Bounty).where(Bounty.id == bounty_id, Bounty.state == BOUNTY_OPEN).values(state=state, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            raise BountyError("that bounty is settled already")
        try:
            await _give_back(session, economy, storage, bounty)
        except (escrow.EscrowError, AllianceError) as error:
            raise BountyError(str(error)) from error
    await session.refresh(bounty)
    return bounty


async def withdraw(session: AsyncSession, economy: Economy, storage: Storage, hero: Hero, bounty_id: int) -> Bounty:
    """The posting team takes an unclaimed bounty back; the reward returns."""
    bounty = await session.get(Bounty, bounty_id)
    if bounty is None or bounty.poster_team_id != await _team_of(session, hero):
        raise BountyError("there is no such bounty of yours")
    return await _close(session, economy, storage, bounty_id, BOUNTY_WITHDRAWN)


async def open_bounties(session: AsyncSession, guild: str | None = None, limit: int = 50) -> list[Bounty]:
    """The bounties still standing, newest first, at $guild or (None) at every guild."""
    query = select(Bounty).where(Bounty.state == BOUNTY_OPEN, Bounty.expires_at > wallclock.timestamp())
    if guild is not None:
        query = query.where(Bounty.guild == guild)
    return list((await session.scalars(query.order_by(Bounty.id.desc()).limit(max(1, min(limit, 100))))).all())


async def _claimants(session: AsyncSession, bounty: Bounty) -> list[int] | None:
    """The teams to pay when the bounty is earned, or None while the target has not fallen. Teams on the other side of a fight in which
    a target hero fell."""
    wanted = set((await session.scalars(select(TeamMember.hero_id).where(TeamMember.team_id == bounty.target_team_id))).all())
    if bounty.scope == "hero":
        wanted &= {bounty.hero_id}
    if not wanted:
        return None
    since = datetime.fromtimestamp(bounty.posted_at, UTC)
    fights = (await session.scalars(
        select(FightParticipant.fight_id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightRecord.created_at >= since, FightParticipant.hero_id.in_(wanted)).distinct()
    )).all()
    fallen: set[int] = set()
    attackers: set[int] = set()
    for fight_id in fights:
        rows = (await session.execute(
            select(FightParticipant.party, FightParticipant.group_index, FightParticipant.character, FightParticipant.hero_id)
            .where(FightParticipant.fight_id == fight_id)
        )).all()
        seats = {(party, group, character): hero_id for party, group, character, hero_id in rows if hero_id in wanted}
        died = {
            tuple(data[:3]) for events in (await session.scalars(select(FightActionRecord.events).where(FightActionRecord.fight_id == fight_id))).all()
            for kind, data in events if kind == EventType.DIED.value
        } & set(seats)
        if not died:
            continue
        fallen |= {seats[seat] for seat in died}
        losing = {seat[0] for seat in died}
        attackers |= {hero_id for party, _group, _character, hero_id in rows if hero_id is not None and party not in losing}
    if not wanted <= fallen or not attackers:
        return None
    teams = (await session.scalars(select(TeamMember.team_id).where(TeamMember.hero_id.in_(attackers)).distinct())).all()
    return sorted(set(teams) - {bounty.target_team_id}) or None


async def judge(session: AsyncSession, economy: Economy, storage: Storage, bounty_id: int) -> Bounty:
    """Pays the bounty if its target has fallen to someone; expires it (the reward goes back) if it ran out. Does nothing to a bounty that
    is settled or still standing."""
    bounty = await session.get(Bounty, bounty_id)
    if bounty is None:
        raise BountyError("there is no such bounty")
    if bounty.state != BOUNTY_OPEN:
        return bounty
    claimants = await _claimants(session, bounty)
    if claimants is None:
        if bounty.expires_at <= wallclock.timestamp():
            return await _close(session, economy, storage, bounty_id, BOUNTY_EXPIRED)
        return bounty
    reward, escrow_id = bounty.reward_gold, bounty.escrow_id
    async with session.begin_nested():
        claimed = await session.execute(
            update(Bounty).where(Bounty.id == bounty_id, Bounty.state == BOUNTY_OPEN)
            .values(state=BOUNTY_PAID, paid_to=claimants, settled_at=wallclock.timestamp())
        )
        if claimed.rowcount == 0:
            return bounty
        try:
            if escrow_id is not None:
                await escrow.release(session, escrow_id)  # the gold is paid out below, to the teams that earned it
            share, extra = divmod(reward, len(claimants))
            for index, team_id in enumerate(claimants):
                await economy.credit_team(session, team_id, share + (extra if index == 0 else 0))
        except escrow.EscrowError as error:
            raise BountyError(str(error)) from error
    await session.refresh(bounty)
    return bounty
