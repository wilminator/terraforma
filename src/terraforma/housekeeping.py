"""Housekeeping: small jobs that tidy up what has gone stale, run on a timer (``Settings.housekeeping_seconds``).

A job is an async function ``(session, now, settings)`` registered with ``@job``; ``run`` gives each its own transaction, so one
failing job is logged and does not stop the others. ``now`` is the wall clock's timestamp (``wallclock``). A job deletes
only what nothing reads any more, so running one twice, or concurrently on two servers, is harmless.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from . import wallclock
from .accounts.ratelimit import ALL_LIMITS
from .fights.models import PendingDrop
from .fights.pending import OPEN, expire
from .models import RateLimitHit
from .settings import Settings

log = logging.getLogger(__name__)

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
async def stale_pending_drops(session: AsyncSession, now: int, settings: Settings) -> int:
    """Pending drops that have waited longer than ``Settings.pending_drop_timeout_seconds`` (0: never): a need/want drop is
    settled with the answers so far, one nobody answered (or a hand-out) is left unclaimed. Returns how many it settled."""
    if not settings.pending_drop_timeout_seconds:
        return 0
    cutoff = wallclock.now() - timedelta(seconds=settings.pending_drop_timeout_seconds)
    stale = await session.scalars(select(PendingDrop).where(PendingDrop.status == OPEN, PendingDrop.created_at <= cutoff).order_by(PendingDrop.id).with_for_update(skip_locked=True))
    count = 0
    for pending in stale.all():
        await expire(session, pending)
        count += 1
    return count


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
