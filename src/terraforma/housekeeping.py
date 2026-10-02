"""Housekeeping: small jobs that tidy up what has gone stale, run on a timer (``Settings.housekeeping_seconds``).

A job is an async function ``(session, now, settings)`` registered with ``@job``; ``run`` gives each its own transaction, so
one failing job is logged and does not stop the others. ``now`` is the wall clock's timestamp (``wallclock``). A job deletes
only what nothing reads any more, so running one twice, or concurrently on two servers, is harmless.
"""

import logging
from collections.abc import Awaitable, Callable

from datetime import timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import wallclock
from .accounts.ratelimit import ALL_LIMITS
from .alliances.models import Ballot, BallotVote, BallotVoter
from .models import RateLimitHit
from .relations.models import ANSWERED, DISMISSED, RatingPrompt
from .settings import Settings

log = logging.getLogger(__name__)

DAY = 24 * 60 * 60
BATCH = 500

Job = Callable[[AsyncSession, int, Settings], Awaitable[int]]

#: Every job, by name, in the order they run. A job returns how many rows it removed.
JOBS: dict[str, Job] = {}


def job(function: Job) -> Job:
    JOBS[function.__name__] = function
    return function


@job
async def finished_rate_limit_windows(session: AsyncSession, now: int, settings: Settings) -> int:
    """Rate-limit counters whose window is over. A subject's old rows also go when it hits the limit again, but a
    one-off address never does, so they would pile up."""
    finished = or_(*(and_(RateLimitHit.bucket == limit.name, RateLimitHit.window_start <= now - limit.window) for limit in ALL_LIMITS))
    return (await session.execute(delete(RateLimitHit).where(finished))).rowcount


@job
async def old_closed_ballots(session: AsyncSession, now: int, settings: Settings) -> int:
    """Ballots closed longer ago than ``ballot_retention_days``, with their votes (0 days: kept for good)."""
    if not settings.ballot_retention_days:
        return 0
    old = (await session.scalars(select(Ballot.id).where(Ballot.closed_at.is_not(None), Ballot.closed_at <= now - settings.ballot_retention_days * DAY))).all()
    removed = 0
    # Ids first, in batches: MySQL refuses a DELETE whose subquery reads the table it deletes from.
    for start in range(0, len(old), BATCH):
        ids = old[start : start + BATCH]
        await session.execute(delete(BallotVote).where(BallotVote.ballot_id.in_(ids)))
        await session.execute(delete(BallotVoter).where(BallotVoter.ballot_id.in_(ids)))
        removed += (await session.execute(delete(Ballot).where(Ballot.id.in_(ids)))).rowcount
    return removed


@job
async def old_settled_rating_prompts(session: AsyncSession, now: int, settings: Settings) -> int:
    """Rating prompts answered or dismissed longer ago than ``rating_prompt_retention_days``; a pending one waits for its
    player however old it is (0 days: kept for good)."""
    if not settings.rating_prompt_retention_days:
        return 0
    before = wallclock.now() - timedelta(days=settings.rating_prompt_retention_days)
    settled = RatingPrompt.state.in_([ANSWERED, DISMISSED]) & (RatingPrompt.updated_at <= before)
    return (await session.execute(delete(RatingPrompt).where(settled))).rowcount


async def run(sessionmaker: async_sessionmaker[AsyncSession], settings: Settings) -> dict[str, int]:
    """One pass: every job in its own transaction. Returns how many rows each removed (a job that failed is left out)."""
    now = wallclock.timestamp()
    removed: dict[str, int] = {}
    for name, work in JOBS.items():
        try:
            async with sessionmaker() as session, session.begin():
                removed[name] = await work(session, now, settings)
        except Exception:  # one bad job must not stop the others
            log.exception("housekeeping job %s failed", name)
    return removed
