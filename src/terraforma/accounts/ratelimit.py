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
TRADE_BY_ACCOUNT = Limit("trade", 120, 60 * 60)
DROP_BY_ACCOUNT = Limit("drop", 120, 60 * 60)
RELATION_BY_ACCOUNT = Limit("relation", 120, 60 * 60)
ALLIANCE_BY_ACCOUNT = Limit("alliance", 120, 60 * 60)
PROFILE_BY_ACCOUNT = Limit("profile", 60, 60 * 60)
# Public pages need no login: this keeps anyone from hunting for tokens.
PUBLIC_PROFILE_BY_ADDRESS = Limit("profile-public", 120, 15 * 60)
# The server-to-server Challenge Token purchase call has no login: every attempt counts against the caller's address.
CHALLENGE_PURCHASE_BY_ADDRESS = Limit("challenge-purchase", 600, 15 * 60)
# Every limit above, for housekeeping: new limits go in here too (a test checks that none is missing).
ALL_LIMITS = [value for value in list(globals().values()) if isinstance(value, Limit)]


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
