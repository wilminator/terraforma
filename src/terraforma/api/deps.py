"""What the calls depend on: a database session, the logged-in account, the caller's address."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account
from .security import SESSION_ACCOUNT, SESSION_VERSION, csrf_matches


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """A session in a transaction: committed when the call succeeds, rolled back when it fails."""
    async with request.app.state.sessionmaker() as session:
        async with session.begin():
            yield session


Db = Annotated[AsyncSession, Depends(get_db)]


async def current_account(request: Request, db: Db) -> Account:
    """For calls that only read: the logged-in account, or 401."""
    account_id = request.session.get(SESSION_ACCOUNT)
    account = await db.get(Account, account_id) if account_id is not None else None
    if account is None or request.session.get(SESSION_VERSION) != account.session_version:
        request.session.clear()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not logged in")
    return account


async def acting_account(request: Request, account: Annotated[Account, Depends(current_account)]) -> Account:
    """For calls that change something: logged in, and the CSRF token matches."""
    if not csrf_matches(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "missing or wrong CSRF token")
    return account


CurrentAccount = Annotated[Account, Depends(current_account)]
ActingAccount = Annotated[Account, Depends(acting_account)]


def client_address(request: Request) -> str:
    """The caller's IP (behind the NAS's proxy, uvicorn's --proxy-headers sets it from X-Forwarded-For)."""
    return request.client.host if request.client else "unknown"
