"""Creating accounts and checking passwords (Argon2id)."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")

_hasher = PasswordHasher()  # Argon2id, the library's RFC 9106 defaults

# Checked against when the username doesn't exist, so a wrong name takes
# as long as a wrong password and can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("not a real password")


def username_key(username: str) -> str:
    """The form usernames are compared in: "Mike" and "mike" are the same account."""
    return username.casefold()


async def create_account(session: AsyncSession, username: str, password: str) -> Account:
    if not USERNAME.fullmatch(username):
        raise ValueError("a username is 3-32 letters, digits, _, . or -")
    if len(password) < 12:
        raise ValueError("a password is at least 12 characters")
    account = Account(username=username, username_key=username_key(username), password_hash=_hasher.hash(password))
    session.add(account)
    await session.flush()
    return account


async def authenticate(session: AsyncSession, username: str, password: str) -> Account | None:
    """The account, if $password is right for $username; else None (either way, the same work is done)."""
    account = await session.scalar(select(Account).where(Account.username_key == username_key(username)))
    try:
        _hasher.verify(account.password_hash if account else _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return None
    if account is None:
        return None
    if _hasher.check_needs_rehash(account.password_hash):
        account.password_hash = _hasher.hash(password)
    return account
