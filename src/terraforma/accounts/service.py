"""Accounts: creating them, confirming email, checking passwords, resetting them (Argon2id)."""

import re

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..models import Account

USERNAME = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
# Deliberately simple: one @, something on each side, a dot in the domain.
# The confirmation link is the real check.
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
PASSWORD_MIN = 12
PASSWORD_MAX = 1024

_hasher = PasswordHasher()  # Argon2id, the library's RFC 9106 defaults

# Checked against when the username doesn't exist, so a wrong name takes
# as long as a wrong password and can't be told apart by timing.
_DUMMY_HASH = _hasher.hash("not a real password")


class AccountError(ValueError):
    """Something the player can fix: the message says what."""


class UsernameTaken(AccountError):
    pass


class EmailTaken(AccountError):
    pass


def username_key(username: str) -> str:
    """The form usernames are compared in: "Mike" and "mike" are the same account."""
    return username.casefold()


def email_key(email: str) -> str:
    return email.strip().casefold()


def check_password(password: str) -> None:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise AccountError(f"a password is {PASSWORD_MIN} to {PASSWORD_MAX} characters")


async def create_account(
    session: AsyncSession, username: str, password: str, *, email: str, confirmed: bool = False
) -> Account:
    """A new account, waiting for its email to be confirmed (unless $confirmed, for tests and admins)."""
    if not USERNAME.fullmatch(username):
        raise AccountError("a username is 3-32 letters, digits, _, . or -")
    email = email.strip()
    if len(email) > 254 or not EMAIL.fullmatch(email):
        raise AccountError("that doesn't look like an email address")
    check_password(password)
    # Hashed first, so "address already registered" takes as long as success.
    password_hash = _hasher.hash(password)
    if await session.scalar(select(Account.id).where(Account.username_key == username_key(username))):
        raise UsernameTaken("that username is taken")
    if await session.scalar(select(Account.id).where(Account.email_key == email_key(email))):
        raise EmailTaken("that email address already has an account")
    account = Account(
        username=username,
        username_key=username_key(username),
        password_hash=password_hash,
        email=email,
        email_key=email_key(email),
        email_confirmed_at=wallclock.now() if confirmed else None,
    )
    session.add(account)
    await session.flush()
    return account


async def find_by_email(session: AsyncSession, email: str) -> Account | None:
    return await session.scalar(select(Account).where(Account.email_key == email_key(email)))


async def confirm_email(session: AsyncSession, account_id: int, email: str) -> Account | None:
    """Marks the address confirmed, if it's still the account's address. The account, or None."""
    account = await session.get(Account, account_id)
    if account is None or account.email_key != email_key(email):
        return None
    if account.email_confirmed_at is None:
        account.email_confirmed_at = wallclock.now()
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


async def reset_password(session: AsyncSession, account_id: int, version: int, password: str) -> Account | None:
    """Sets a new password and ends every login, if $version is still current (each reset link works once)."""
    check_password(password)
    account = await session.get(Account, account_id)
    if account is None or account.session_version != version:
        return None
    account.password_hash = _hasher.hash(password)
    account.session_version += 1
    # Getting the reset mail proves the address, as the confirmation link would.
    if account.email_confirmed_at is None:
        account.email_confirmed_at = wallclock.now()
    return account
