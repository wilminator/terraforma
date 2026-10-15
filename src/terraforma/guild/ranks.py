"""A team's points and rank with a guild, and the gate a guild puts on a party. A game awards points and ranks from the dialog
tags ``add_guild_points`` and ``set_guild_rank``; ``guild_points``, ``guild_rank`` and ``party_gate`` read them.

A rank is a whole number, 0 the lowest. The game names its ranks in its own text (Vanguard Tavern: E is 0, D 1, C 2, B 3, A 4, S 5).
Points only ever grow, and rank changes only when the dialog sets it (a promotion quest), so a team can have the points for a
rank and not hold it yet.
"""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import GuildStanding

MAX_RANK = 99
MAX_POINTS = 1_000_000_000


class RankError(ValueError):
    """Something the caller can fix: the message says what."""


async def standing(session: AsyncSession, team_id: int, guild: str) -> tuple[int, int]:
    """The team's (points, rank) with the guild: (0, 0) before it has any."""
    row = await session.scalar(select(GuildStanding).where(GuildStanding.team_id == team_id, GuildStanding.guild == guild))
    return (0, 0) if row is None else (row.points, row.rank)


async def meets_any(session: AsyncSession, team_id: int, requirements: list[tuple[str, int]]) -> bool:
    """Whether the team holds at least the asked rank with at least one of the guilds in $requirements, a list of (guild, lowest
    rank). A guild asks for the rank its own game names (kingdom B is 3, a mercenary company's A is 4), so "rank B in a kingdom
    or A in a company" is two entries. No requirements pass no one."""
    return any([(await standing(session, team_id, guild))[1] >= lowest for guild, lowest in requirements])


async def _row(session: AsyncSession, team_id: int, guild: str) -> GuildStanding:
    if not guild:
        raise RankError("a guild has a name")
    for _ in range(2):  # (two first awards at the same instant can both find no row: the loser uses the winner's)
        row = await session.scalar(select(GuildStanding).where(GuildStanding.team_id == team_id, GuildStanding.guild == guild))
        if row is not None:
            return row
        try:
            async with session.begin_nested():
                row = GuildStanding(team_id=team_id, guild=guild, points=0, rank=0)
                session.add(row)
                await session.flush()
            return row
        except IntegrityError:
            continue
    raise RankError("could not record that standing, try again")


async def add_points(session: AsyncSession, team_id: int, guild: str, points: int) -> int:
    """The team earned $points with the guild. Returns its new total (capped at a billion)."""
    if points < 1:
        raise RankError("a guild awards at least 1 point at a time")
    row = await _row(session, team_id, guild)
    row.points = min(MAX_POINTS, row.points + points)
    await session.flush()
    return row.points


async def set_rank(session: AsyncSession, team_id: int, guild: str, rank: int) -> None:
    """The team holds this rank with the guild now (a promotion, or a demotion if the game wants one)."""
    if not 0 <= rank <= MAX_RANK:
        raise RankError(f"a rank is a whole number from 0 to {MAX_RANK}")
    row = await _row(session, team_id, guild)
    row.rank = rank
    await session.flush()


async def forget_team(session: AsyncSession, team_id: int) -> None:
    """A team that is deleted takes its standing with it."""
    await session.execute(delete(GuildStanding).where(GuildStanding.team_id == team_id))


@dataclass(frozen=True)
class Gate:
    """What a guild asks of a party for a quest. 0 asks nothing. The ranks are the party's teams' ranks with that guild; the PXP is the party's
    (``Rules.pxp`` added up over the heroes of the teams)."""

    average: Decimal = Decimal(0)  # the teams' ranks, on average, are at least this
    lowest: int = 0  # every team is at least this rank
    highest: int = 0  # the best team is at least this rank
    pxp_min: int = 0
    pxp_max: int = 0  # (0: no ceiling)


def refusal(gate: Gate, ranks: list[int], pxp: int) -> str | None:
    """Why a party whose teams have these $ranks and whose strength is $pxp does not pass the gate, or None if it does. A party
    with no teams passes nothing."""
    if not ranks:
        return "there is no party"
    if sum(ranks) < gate.average * len(ranks):
        return "the party's average rank is too low"
    if min(ranks) < gate.lowest:
        return "a team of the party is below the lowest rank allowed"
    if max(ranks) < gate.highest:
        return "no team of the party has the rank asked of the best"
    if pxp < gate.pxp_min:
        return "the party is too weak"
    if gate.pxp_max and pxp > gate.pxp_max:
        return "the party is too strong"
    return None


async def party_pxp(session: AsyncSession, rules, team_ids: list[int]) -> int:
    """A party's strength: the PXP (``Rules.pxp``) of every hero of the teams, added up."""
    from ..fights.build import hero_fighter  # here, since fights reads the heroes
    from ..heroes.models import Hero, TeamMember

    ids = select(TeamMember.hero_id).where(TeamMember.team_id.in_(team_ids))
    total = 0
    for hero in (await session.scalars(select(Hero).where(Hero.id.in_(ids)).order_by(Hero.id))).all():
        total += rules.pxp(await hero_fighter(session, hero, rules))
    return total
