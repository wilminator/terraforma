"""Ballots: an alliance puts a question to its teams, and the engine counts.

The tooling, not the politics: a role that may (``open_ballot``) puts a title and a few options to the alliance, the
teams whose roles may ``vote`` each vote once (weighted as the game says: ``Alliances.vote_weight``), and the ballot
closes when its time is up, when everyone who could vote has, or when a role that may (``close_ballot``) closes it. The
game decides who wins (``Alliances.decide``) and what follows (``Alliances.on_ballot_closed``, from the ballot's ``kind`` and
``payload``, which the engine keeps and never reads). Nothing waits on a timer: a ballot is closed, if its time is up, the
next time anyone looks at it or votes, and ``close_due`` closes every ballot that is due, for a game to call on its own schedule.

*Public* ballots show who voted for what, and a team may change its vote until it closes. *Secret* ballots keep who has
voted (``BallotVoter``) apart from what was voted (``BallotVote``, which carries no team), show only the turnout until
they close and the totals after, and a vote on one is final, since no one, the server included, can say which vote is whose.
"""

import json

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..heroes.models import Team
from . import service
from .hooks import Alliances
from .models import Alliance, AllianceMember, Ballot, BallotVote, BallotVoter

TITLE_MAX = 120
OPTION_MAX = 60
KIND_MAX = 32
PAYLOAD_MAX = 2000
LONGEST = 30 * 24 * 3600
SHORTEST = 60


class BallotError(service.AllianceError):
    pass


class BallotNotFound(service.NotFound):
    pass


# --- finding and counting ------------------------------------------------------------------------------------------------

async def get_ballot(session: AsyncSession, alliance: Alliance, ballot_id: int) -> Ballot:
    ballot = await session.get(Ballot, ballot_id)
    if ballot is None or ballot.alliance_id != alliance.id:
        raise BallotNotFound("there's no such ballot")
    return ballot


async def _eligible(session: AsyncSession, hooks: Alliances, alliance: Alliance) -> list[tuple[AllianceMember, int]]:
    """The member teams that may vote, each with its weight (a weight of 0 may not)."""
    eligible = []
    for member in await service.members(session, alliance.id):
        if await hooks.allowed(session, alliance, member, "vote"):
            weight = await hooks.vote_weight(session, alliance, member)
            if weight > 0:
                eligible.append((member, weight))
    return eligible


async def _turnout(session: AsyncSession, ballot: Ballot) -> list[int]:
    return list((await session.scalars(select(BallotVoter.team_id).where(BallotVoter.ballot_id == ballot.id))).all())


async def _totals(session: AsyncSession, ballot: Ballot) -> list[int]:
    totals = [0] * len(ballot.options)
    rows = await session.execute(select(BallotVote.option, func.sum(BallotVote.weight)).where(BallotVote.ballot_id == ballot.id).group_by(BallotVote.option))
    for option, weight in rows.all():
        totals[option] = int(weight)
    return totals


async def _close(session: AsyncSession, hooks: Alliances, alliance: Alliance, ballot: Ballot) -> None:
    eligible = await _eligible(session, hooks, alliance)
    totals = await _totals(session, ballot)
    winner = await hooks.decide(session, alliance, ballot, totals, sum(weight for _, weight in eligible))
    if winner is not None and not 0 <= winner < len(ballot.options):
        raise BallotError("the game's rules named an option that is not on the ballot")
    ballot.result = {"winner": winner, "totals": totals, "turnout": len(await _turnout(session, ballot)), "eligible": len(eligible)}
    ballot.closed_at = wallclock.timestamp()
    await session.flush()
    await hooks.on_ballot_closed(session, alliance, ballot, dict(ballot.result))


async def refresh(session: AsyncSession, hooks: Alliances, alliance: Alliance, ballot: Ballot) -> Ballot:
    """Closes the ballot if its time is up or (when the game says so) everyone who could vote has."""
    if ballot.closed_at is not None:
        return ballot
    if ballot.closes_at is not None and wallclock.timestamp() >= ballot.closes_at:
        await _close(session, hooks, alliance, ballot)
    elif hooks.close_when_all_voted:
        eligible = {member.team_id for member, _ in await _eligible(session, hooks, alliance)}
        if eligible and eligible <= set(await _turnout(session, ballot)):
            await _close(session, hooks, alliance, ballot)
    return ballot


async def close_due(session: AsyncSession, hooks_for) -> int:
    """Closes every open ballot whose time is up; returns how many. ``hooks_for(alliance)`` gives the alliance's rules
    (a game's ``Alliances``). For whatever runs the game's schedule."""
    due = (await session.scalars(select(Ballot).where(Ballot.closed_at.is_(None), Ballot.closes_at.is_not(None), Ballot.closes_at <= wallclock.timestamp()))).all()
    for ballot in due:
        alliance = await session.get(Alliance, ballot.alliance_id)
        await _close(session, hooks_for(alliance), alliance, ballot)
    return len(due)


# --- opening, voting, closing -------------------------------------------------------------------------------------------------

def check_options(hooks: Alliances, title: str, options: list[str]) -> tuple[str, list[str]]:
    title = " ".join(title.split())
    if not 1 <= len(title) <= TITLE_MAX:
        raise BallotError(f"a title is 1 to {TITLE_MAX} characters")
    cleaned = [" ".join(option.split()) for option in options]
    low, high = hooks.option_limits
    if not low <= len(cleaned) <= high:
        raise BallotError(f"a ballot has {low} to {high} options")
    if any(not 1 <= len(option) <= OPTION_MAX for option in cleaned):
        raise BallotError(f"an option is 1 to {OPTION_MAX} characters")
    if len({option.casefold() for option in cleaned}) != len(cleaned):
        raise BallotError("the options must differ")
    return title, cleaned


async def open_ballot(
    session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, title: str, options: list[str],
    *, kind: str = "", payload: dict | None = None, secret: bool = False, closes_in: int | None = None,
) -> Ballot:
    """Puts a question to the alliance. ``closes_in`` is seconds from now (None: it closes when everyone has voted or a role that may closes it)."""
    await service._acting(session, hooks, alliance, actor_team_id, "open_ballot")
    title, options = check_options(hooks, title, options)
    if len(kind) > KIND_MAX:
        raise BallotError(f"a kind is at most {KIND_MAX} characters")
    if payload is not None and len(json.dumps(payload)) > PAYLOAD_MAX:
        raise BallotError("the payload is too large")
    if closes_in is not None and not SHORTEST <= closes_in <= LONGEST:
        raise BallotError("a ballot stays open for a minute to 30 days")
    for each in (await session.scalars(select(Ballot).where(Ballot.alliance_id == alliance.id, Ballot.closed_at.is_(None)))).all():
        await refresh(session, hooks, alliance, each)
    open_now = await session.scalar(select(func.count()).select_from(Ballot).where(Ballot.alliance_id == alliance.id, Ballot.closed_at.is_(None)))
    if open_now >= hooks.max_open_ballots:
        raise BallotError(f"an alliance has at most {hooks.max_open_ballots} ballots open")
    now = wallclock.timestamp()
    ballot = Ballot(
        alliance_id=alliance.id, kind=kind, title=title, options=options, payload=payload, secret=secret,
        opened_by_team_id=actor_team_id, opened_at=now, closes_at=None if closes_in is None else now + closes_in,
    )
    session.add(ballot)
    await session.flush()
    return ballot


async def cast(session: AsyncSession, hooks: Alliances, alliance: Alliance, team_id: int, ballot: Ballot, option: int) -> Ballot:
    """The team votes for an option. On a public ballot it may vote again to change its vote until the ballot closes."""
    await refresh(session, hooks, alliance, ballot)
    if ballot.closed_at is not None:
        raise BallotError("that ballot is closed")
    member = await service.member_row(session, alliance.id, team_id)
    if member is None:
        raise service.Forbidden("that team is not in the alliance")
    weight = await hooks.vote_weight(session, alliance, member) if await hooks.allowed(session, alliance, member, "vote") else 0
    if weight <= 0:
        raise service.Forbidden("your role can't vote")
    if not 0 <= option < len(ballot.options):
        raise BallotError("there's no such option")
    voted = await session.scalar(select(BallotVoter).where(BallotVoter.ballot_id == ballot.id, BallotVoter.team_id == team_id))
    if voted is not None:
        if ballot.secret:
            raise BallotError("a vote in a secret ballot is final")
        earlier = await session.scalar(select(BallotVote).where(BallotVote.ballot_id == ballot.id, BallotVote.team_id == team_id))
        earlier.option, earlier.weight = option, weight
    else:
        try:
            async with session.begin_nested():
                session.add(BallotVoter(ballot_id=ballot.id, team_id=team_id))
                session.add(BallotVote(ballot_id=ballot.id, team_id=None if ballot.secret else team_id, option=option, weight=weight))
                await session.flush()
        except IntegrityError as error:  # the same team voting at the same instant
            raise BallotError("that team has voted already") from error
    await session.flush()
    return await refresh(session, hooks, alliance, ballot)


async def close(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, ballot: Ballot) -> Ballot:
    await service._acting(session, hooks, alliance, actor_team_id, "close_ballot")
    await refresh(session, hooks, alliance, ballot)
    if ballot.closed_at is not None:
        raise BallotError("that ballot is closed")
    await _close(session, hooks, alliance, ballot)
    return ballot


# --- what the player sees ----------------------------------------------------------------------------------------------------------

async def view(session: AsyncSession, hooks: Alliances, alliance: Alliance, ballot: Ballot, viewer_team_id: int) -> dict:
    await refresh(session, hooks, alliance, ballot)
    voters = await _turnout(session, ballot)
    shown = {
        "id": ballot.id, "kind": ballot.kind, "title": ballot.title, "options": list(ballot.options), "payload": ballot.payload,
        "secret": ballot.secret, "opened_at": ballot.opened_at, "closes_at": ballot.closes_at, "closed": ballot.closed_at is not None,
        "turnout": len(voters), "you_voted": viewer_team_id in voters,
    }
    if ballot.closed_at is not None:
        shown["result"] = ballot.result
    if not ballot.secret:
        votes = (await session.scalars(select(BallotVote).where(BallotVote.ballot_id == ballot.id).order_by(BallotVote.id))).all()
        names = {team.id: team.name for team in (await session.scalars(select(Team).where(Team.id.in_([vote.team_id for vote in votes])))).all()}
        shown["votes"] = [{"team_id": vote.team_id, "team": names.get(vote.team_id), "option": vote.option, "weight": vote.weight} for vote in votes]
        shown["totals"] = await _totals(session, ballot)
        shown["your_option"] = next((vote.option for vote in votes if vote.team_id == viewer_team_id), None)
    elif ballot.closed_at is not None:
        shown["totals"] = ballot.result["totals"]
    return shown


async def listing(session: AsyncSession, hooks: Alliances, alliance: Alliance, viewer_team_id: int, limit: int = 20) -> list[dict]:
    """The alliance's ballots, open ones first and newest first, then the latest that have closed."""
    rows = (await session.scalars(select(Ballot).where(Ballot.alliance_id == alliance.id).order_by(Ballot.id.desc()).limit(limit * 3))).all()
    shown = [await view(session, hooks, alliance, ballot, viewer_team_id) for ballot in rows]
    return (sorted(shown, key=lambda each: each["closed"]))[:limit]
