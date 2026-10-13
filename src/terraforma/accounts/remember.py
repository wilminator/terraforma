"""Remembered browsers: a browser that proved a second factor may skip it for the number of days the game sets
(``SignIn.remember_browser_days``).

The browser keeps a random secret in a cookie of its own (one per account, so two players on one machine do not
replace each other's); the table keeps only its hash. A remembered browser stops counting when its time is up, when the
account's session version moves on (a password reset or an email change logs everyone out), and whenever the account's
two-factor login changes: a new method, a removed one, new recovery codes, turning it off.
"""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..models import Account, RememberedBrowser

#: The most browsers one account keeps; remembering another forgets the oldest.
MAX_BROWSERS = 10
DAY = 24 * 60 * 60


def cookie_name(account_id: int) -> str:
    return f"tf_remember_{account_id}"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _label(request: Request) -> str:
    """The browser's own words for itself, cut short: enough for the player to tell their browsers apart."""
    agent = " ".join(request.headers.get("user-agent", "").split())
    return agent[:80] or "a browser"


def aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def is_remembered(request: Request, db: AsyncSession, account: Account) -> bool:
    """Whether this browser may skip the second factor for $account."""
    secret = request.cookies.get(cookie_name(account.id))
    if not secret or len(secret) > 100:
        return False
    row = await db.scalar(
        select(RememberedBrowser).where(RememberedBrowser.account_id == account.id, RememberedBrowser.secret_hash == _hash(secret))
    )
    if row is None or row.session_version != account.session_version or aware(row.expires_at) <= wallclock.now():
        return False
    row.last_used_at = wallclock.now()
    return True


async def remember(request: Request, response: Response, db: AsyncSession, account: Account, days: int) -> None:
    """Remembers this browser for $days days (a cookie for the browser, a row for the server)."""
    if days <= 0:
        return
    secret = secrets.token_urlsafe(32)
    db.add(
        RememberedBrowser(
            account_id=account.id,
            secret_hash=_hash(secret),
            session_version=account.session_version,
            expires_at=wallclock.now() + timedelta(days=days),
            label=_label(request),
        )
    )
    await db.flush()
    rows = (
        await db.scalars(select(RememberedBrowser).where(RememberedBrowser.account_id == account.id).order_by(RememberedBrowser.id.desc()))
    ).all()
    for old in rows[MAX_BROWSERS:]:
        await db.delete(old)
    response.set_cookie(
        cookie_name(account.id),
        secret,
        max_age=days * DAY,
        path="/api",
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.secure_cookies,
    )


async def forget(db: AsyncSession, account: Account, browser_id: int | None = None) -> int:
    """Forgets one browser (by its row id) or, without an id, every one. Returns how many."""
    query = delete(RememberedBrowser).where(RememberedBrowser.account_id == account.id)
    if browser_id is not None:
        query = query.where(RememberedBrowser.id == browser_id)
    return (await db.execute(query)).rowcount


async def listing(db: AsyncSession, account: Account, request: Request) -> list[dict]:
    """The account's remembered browsers that still count, newest first; ``this`` marks the one asking."""
    mine = request.cookies.get(cookie_name(account.id))
    mine_hash = _hash(mine) if mine else None
    rows = await db.scalars(
        select(RememberedBrowser)
        .where(
            RememberedBrowser.account_id == account.id,
            RememberedBrowser.session_version == account.session_version,
            RememberedBrowser.expires_at > wallclock.now(),
        )
        .order_by(RememberedBrowser.id.desc())
    )
    return [
        {
            "id": row.id,
            "label": row.label,
            "since": aware(row.created_at).isoformat(),
            "expires": aware(row.expires_at).isoformat(),
            "this": row.secret_hash == mine_hash,
        }
        for row in rows
    ]
