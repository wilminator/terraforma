"""Standing statuses: long-term status tokens that live between fights.

A fight's own tokens last a number of rounds and go when the fight does. A standing status is placed by a dialog (``add_status``),
on a hero, a team or a party, lasts until a wall-clock end time (the dialog gives the seconds it lasts; none: until removed),
and may be unremovable by a buff-cancelling effect. When a fight starts, the live standing statuses of each hero, of its team
and of its party go onto that fighter as ordinary tokens, for the whole fight (``attach``): their end time only matters between
fights. A token is its own source (a standing status has nobody who placed it in the fight). One that has ended is gone at once
(every read leaves it out); ``sweep`` removes the rows. What an ended status should lead to (a guest team leaving a party, say)
is the game's: ``ended`` lists the rows a sweep removed so a caller can tell it.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..content.models import Status
from ..fights.build import known_statuses
from ..fights.fight import Fight
from ..fights.status import StatusToken
from ..heroes.models import Hero, TeamMember
from ..parties import service as parties
from .models import HERO, KINDS, PARTY, TEAM, StandingStatus

MAX_SECONDS = 10 * 365 * 24 * 3600


class StandingError(ValueError):
    """Something the caller can fix: the message says what."""


def _live():
    return or_(StandingStatus.ends_at.is_(None), StandingStatus.ends_at > wallclock.now())


async def place(session: AsyncSession, kind: str, target_id: int, status: str, seconds: int | None, unremovable: bool = False) -> StandingStatus:
    """Puts the status on the target for $seconds from now (None: until removed), renewing it if it is there already.
    Raises StandingError for a status the content does not have."""
    if kind not in KINDS:
        raise StandingError(f"a standing status goes on one of {', '.join(KINDS)}")
    if seconds is not None and not 1 <= seconds <= MAX_SECONDS:
        raise StandingError("a status lasts at least a second and at most ten years")
    if await session.scalar(select(Status.id).where(Status.key == status, Status.active.is_(True))) is None:
        raise StandingError(f"there is no status {status!r}")
    ends = None if seconds is None else wallclock.now() + timedelta(seconds=seconds)
    row = await session.scalar(select(StandingStatus).where(StandingStatus.target_kind == kind, StandingStatus.target_id == target_id, StandingStatus.status == status))
    if row is None:
        row = StandingStatus(target_kind=kind, target_id=target_id, status=status)
        session.add(row)
    row.ends_at, row.unremovable = ends, unremovable
    await session.flush()
    return row


async def has(session: AsyncSession, kind: str, target_id: int, status: str) -> bool:
    return await session.scalar(
        select(StandingStatus.id).where(StandingStatus.target_kind == kind, StandingStatus.target_id == target_id, StandingStatus.status == status, _live()).limit(1)
    ) is not None


async def remaining(session: AsyncSession, kind: str, target_id: int, status: str) -> int | None:
    """Whole seconds a live status has left (None: it has no end, or it is not there)."""
    row = await session.scalar(
        select(StandingStatus).where(StandingStatus.target_kind == kind, StandingStatus.target_id == target_id, StandingStatus.status == status, _live())
    )
    if row is None or row.ends_at is None:
        return None
    ends = row.ends_at if row.ends_at.tzinfo else row.ends_at.replace(tzinfo=wallclock.now().tzinfo)
    return max(0, int((ends - wallclock.now()).total_seconds()))


async def of_hero(session: AsyncSession, hero: Hero) -> list[dict]:
    """What a hero is under now: its own, its team's and its party's live standing statuses (each with where it sits)."""
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    party = await parties.party_of(session, team_id) if team_id is not None else None
    targets = [(HERO, hero.id)] + ([(TEAM, team_id)] if team_id is not None else []) + ([(PARTY, party.id)] if party is not None else [])
    out = []
    for kind, target_id in targets:
        rows = await session.scalars(
            select(StandingStatus).where(StandingStatus.target_kind == kind, StandingStatus.target_id == target_id, _live()).order_by(StandingStatus.id)
        )
        for row in rows.all():
            out.append({"on": kind, "status": row.status, "unremovable": row.unremovable, "seconds_left": await remaining(session, kind, target_id, row.status)})
    return out


async def forget(session: AsyncSession, kind: str, target_id: int) -> None:
    """The target is gone: so are its statuses."""
    await session.execute(delete(StandingStatus).where(StandingStatus.target_kind == kind, StandingStatus.target_id == target_id))


async def sweep(session: AsyncSession, now: datetime | None = None) -> list[tuple[str, int, str]]:
    """Removes the standing statuses that have ended, and returns them as ``(kind, target id, status)``."""
    cutoff = now or wallclock.now()
    rows = (await session.scalars(select(StandingStatus).where(StandingStatus.ends_at.is_not(None), StandingStatus.ends_at <= cutoff).order_by(StandingStatus.id))).all()
    gone = [(row.target_kind, row.target_id, row.status) for row in rows]
    for row in rows:
        await session.delete(row)
    await session.flush()
    return gone


async def attach(session: AsyncSession, fight: Fight, side: int = 0) -> None:
    """Puts the live standing statuses of each hero on $side, of its team and of its party, on its fighter as tokens that last
    the whole fight (unremovable if the status is). A status the fight does not know is skipped; the same status from
    several places is one token, unremovable if any is."""
    known = fight.statuses or await known_statuses(session)
    for group_number, group in fight.parties[side].groups.items():
        for number, fighter in group.characters.items():
            if fighter.charid is None:
                continue
            hero = await session.get(Hero, fighter.charid)
            if hero is None:
                continue
            address = (side, group_number, number)
            for row in await of_hero(session, hero):
                spec = known.get(row["status"])
                if spec is None:
                    continue
                token = next((each for each in fighter.tokens if each.spec.key == spec.key and each.source == address), None)
                if token is None:
                    fighter.tokens.append(StatusToken(spec, address, None, unremovable=row["unremovable"]))
                else:
                    token.unremovable = token.unremovable or row["unremovable"]
