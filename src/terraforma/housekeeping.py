"""Housekeeping: small jobs that tidy up what has gone stale, run on a timer (``Settings.housekeeping_seconds``).

A job is an async function ``(session, now)`` registered with ``@job``; ``run`` gives each its own transaction, so one
failing job is logged and does not stop the others. ``now`` is the wall clock's timestamp (``wallclock``). A job deletes
only what nothing reads any more, so running one twice, or concurrently on two servers, is harmless.
"""

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import and_, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import wallclock
from .accounts.ratelimit import ALL_LIMITS
from .models import RateLimitHit

log = logging.getLogger(__name__)

Job = Callable[[AsyncSession, int], Awaitable[int]]

#: Every job, by name, in the order they run. A job returns how many rows it removed.
JOBS: dict[str, Job] = {}


def job(function: Job) -> Job:
    JOBS[function.__name__] = function
    return function


@job
async def finished_rate_limit_windows(session: AsyncSession, now: int) -> int:
    """Rate-limit counters whose window is over. A subject's old rows also go when it hits the limit again, but a
    one-off address never does, so they would pile up."""
    finished = or_(*(and_(RateLimitHit.bucket == limit.name, RateLimitHit.window_start <= now - limit.window) for limit in ALL_LIMITS))
    return (await session.execute(delete(RateLimitHit).where(finished))).rowcount


async def run(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    """One pass: every job in its own transaction. Returns how many rows each removed (a job that failed is left out)."""
    now = wallclock.timestamp()
    removed: dict[str, int] = {}
    for name, work in JOBS.items():
        try:
            async with sessionmaker() as session, session.begin():
                removed[name] = await work(session, now)
        except Exception:  # one bad job must not stop the others
            log.exception("housekeeping job %s failed", name)
    return removed
