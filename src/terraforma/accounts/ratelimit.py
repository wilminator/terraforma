"""Rate limits, kept in the database so they hold on every database and across restarts.

Each limit counts attempts per subject (an IP address, a username, an
email address, always stored hashed) in fixed windows of time. Over the limit,
the attempt is refused until the window ends.
"""

import hashlib
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import wallclock
from ..db.dialect import increment
from ..models import RateLimitHit


@dataclass(frozen=True)
class Limit:
    name: str
    attempts: int
    window: int  # seconds


LOGIN_BY_ADDRESS = Limit("login-ip", 20, 15 * 60)
LOGIN_BY_NAME = Limit("login-name", 10, 15 * 60)
REGISTER_BY_ADDRESS = Limit("register-ip", 5, 60 * 60)
RESET_BY_ADDRESS = Limit("reset-ip", 10, 60 * 60)
RESET_BY_EMAIL = Limit("reset-email", 3, 60 * 60)
# Every guess at a 2FA code, at login or in the 2FA calls, counts against the account.
TWOFA_CODE_BY_ACCOUNT = Limit("2fa-code", 10, 15 * 60)
TWOFA_CHANGE_BY_ACCOUNT = Limit("2fa-change", 5, 60 * 60)
EMAIL_CHANGE_BY_ACCOUNT = Limit("email-change", 5, 60 * 60)


class RateLimited(Exception):
    def __init__(self, retry_after: int):
        super().__init__(f"too many attempts: try again in {retry_after} seconds")
        self.retry_after = retry_after


def _key(value: str) -> str:
    return hashlib.sha256(value.casefold().encode()).hexdigest()


async def hit(sessionmaker: async_sessionmaker[AsyncSession], limit: Limit, value: str) -> None:
    """Counts an attempt for $value under $limit; raises RateLimited once over it.

    Counted in its own transaction, so the attempt stays counted even when
    the call it guards fails (a wrong password, say).
    """
    now = wallclock.timestamp()
    window = now - now % limit.window
    subject = _key(value)
    async with sessionmaker() as session, session.begin():
        # Old windows for this subject are finished with.
        await session.execute(
            delete(RateLimitHit).where(
                RateLimitHit.bucket == limit.name, RateLimitHit.subject == subject, RateLimitHit.window_start < window
            )
        )
        await increment(
            session, RateLimitHit.__table__, {"bucket": limit.name, "subject": subject, "window_start": window}, column="hits"
        )
        hits = await session.scalar(
            select(RateLimitHit.hits).where(
                RateLimitHit.bucket == limit.name, RateLimitHit.subject == subject, RateLimitHit.window_start == window
            )
        )
    if hits > limit.attempts:
        raise RateLimited(retry_after=window + limit.window - now)
