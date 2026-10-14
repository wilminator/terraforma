"""The sign-in provider's rules: checking an authorization request, making and trading codes, subject ids, access tokens.

Everything here works on the database, so any server of a cluster can answer any step. Functions that must leave a change
behind even when they refuse (a replayed code revokes its tokens) return the refusal instead of raising it.
"""

import base64
import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..keys import KeyRing
from ..models import Account
from ..settings import OidcClient, Settings
from . import signing
from .models import OidcCode, OidcSubject, OidcToken

CODE_SECONDS = 60
ID_TOKEN_SECONDS = 10 * 60
ACCESS_TOKEN_SECONDS = 60 * 60
SCOPES = ("openid", "profile")
CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")  # base64url of a SHA-256
VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
ENDPOINTS = {"authorization_endpoint": "/oidc/authorize", "token_endpoint": "/oidc/token", "userinfo_endpoint": "/oidc/userinfo", "jwks_uri": "/oidc/jwks"}


def issuer(settings: Settings) -> str:
    return settings.public_url.rstrip("/")


def find_client(settings: Settings, client_id: str) -> OidcClient | None:
    return next((client for client in settings.oidc_clients if client.client_id == client_id), None)


def check_secret(client: OidcClient | None, given: str) -> bool:
    """Whether $given is the client's secret. Takes the same time for an unknown client."""
    expected = client.client_secret if client else "x" * 32
    right = hmac.compare_digest(given.encode(), expected.encode())
    return right and client is not None


def hashed(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode("ascii")


def granted_scope(requested: str) -> str | None:
    """The scopes we give out of those asked for (others are ignored, as the standard says), or None without ``openid``."""
    asked = requested.split()
    if "openid" not in asked:
        return None
    return " ".join(scope for scope in SCOPES if scope in asked)


def with_parameters(address: str, **parameters: str | None) -> str:
    """$address with the parameters added to its query (those that are None left out)."""
    parts = urlsplit(address)
    query = parse_qsl(parts.query, keep_blank_values=True) + [(name, value) for name, value in parameters.items() if value is not None]
    return urlunsplit(parts._replace(query=urlencode(query)))


# --- subject ids ------------------------------------------------------------------------------------------------------

async def subject_for(session: AsyncSession, account_id: int, client_id: str) -> str:
    """The opaque id $client_id knows the account by; made, once, the first time it is asked for."""
    row = await session.get(OidcSubject, (account_id, client_id))
    if row is not None:
        return row.subject
    try:
        async with session.begin_nested():
            session.add(OidcSubject(account_id=account_id, client_id=client_id, subject=secrets.token_urlsafe(32)))
            await session.flush()
    except IntegrityError:  # another call made it a moment ago
        pass
    return (await session.get(OidcSubject, (account_id, client_id), populate_existing=True)).subject


# --- codes --------------------------------------------------------------------------------------------------------------

async def make_code(
    session: AsyncSession, account: Account, client: OidcClient, redirect_uri: str, scope: str, challenge: str, nonce: str | None
) -> str:
    code = secrets.token_urlsafe(32)
    session.add(
        OidcCode(
            code_hash=hashed(code),
            client_id=client.client_id,
            account_id=account.id,
            redirect_uri=redirect_uri,
            challenge=challenge,
            nonce=nonce,
            scope=scope,
            session_version=account.session_version,
            expires_at=wallclock.timestamp() + CODE_SECONDS,
        )
    )
    await session.flush()
    return code


@dataclass(frozen=True)
class Refused:
    """A token call that failed: the standard's error name and a sentence for the site's developer."""

    error: str
    description: str


@dataclass(frozen=True)
class Issued:
    access_token: str
    expires_in: int
    id_token: str
    scope: str


async def trade_code(
    session: AsyncSession, ring: KeyRing, settings: Settings, client: OidcClient, code: str, redirect_uri: str, verifier: str
) -> Issued | Refused:
    """Trades a code for tokens. A code works once: it is spent whatever else is wrong with the call, and a code shown a
    second time revokes the tokens it made."""
    code_hash = hashed(code)
    row = await session.get(OidcCode, code_hash)
    if row is None or row.client_id != client.client_id:
        return Refused("invalid_grant", "that code is not valid")
    claimed = await session.execute(update(OidcCode).where(OidcCode.code_hash == code_hash, OidcCode.used.is_(False)).values(used=True))
    if claimed.rowcount != 1:
        await session.execute(delete(OidcToken).where(OidcToken.code_hash == code_hash))
        return Refused("invalid_grant", "that code has already been used")
    if row.expires_at <= wallclock.timestamp():
        return Refused("invalid_grant", "that code has expired")
    if row.redirect_uri != redirect_uri:
        return Refused("invalid_grant", "redirect_uri is not the one the sign-in started with")
    if not VERIFIER.fullmatch(verifier) or not hmac.compare_digest(pkce_challenge(verifier).encode(), row.challenge.encode()):
        return Refused("invalid_grant", "code_verifier does not match the code_challenge")
    account = await session.get(Account, row.account_id)
    if account is None or account.session_version != row.session_version:
        return Refused("invalid_grant", "that sign-in has ended")
    now = wallclock.timestamp()
    subject = await subject_for(session, account.id, client.client_id)
    claims = {"iss": issuer(settings), "sub": subject, "aud": client.client_id, "iat": now, "exp": now + ID_TOKEN_SECONDS}
    if row.nonce is not None:
        claims["nonce"] = row.nonce
    if "profile" in row.scope.split() and account.handle:
        claims["preferred_username"] = account.handle
    token = secrets.token_urlsafe(32)
    session.add(
        OidcToken(
            token_hash=hashed(token),
            client_id=client.client_id,
            account_id=account.id,
            scope=row.scope,
            session_version=account.session_version,
            code_hash=code_hash,
            expires_at=now + ACCESS_TOKEN_SECONDS,
        )
    )
    await session.flush()
    return Issued(access_token=token, expires_in=ACCESS_TOKEN_SECONDS, id_token=signing.sign(ring, claims), scope=row.scope)


# --- the userinfo call ----------------------------------------------------------------------------------------------------

async def userinfo(session: AsyncSession, token: str) -> dict | None:
    """The claims the access token allows, or None if it is unknown, expired, or its player's logins have all ended."""
    row = await session.get(OidcToken, hashed(token))
    if row is None or row.expires_at <= wallclock.timestamp():
        return None
    account = await session.get(Account, row.account_id)
    if account is None or account.session_version != row.session_version:
        return None
    claims = {"sub": await subject_for(session, account.id, row.client_id)}
    if "profile" in row.scope.split() and account.handle:
        claims["preferred_username"] = account.handle
    return claims


# --- housekeeping -----------------------------------------------------------------------------------------------------------

async def remove_expired(session: AsyncSession, now: int) -> int:
    """Codes and tokens that have run out. A code is kept until then even when spent, to know a replay."""
    count = 0
    for table in (OidcCode, OidcToken):
        count += (await session.execute(delete(table).where(table.expires_at <= now))).rowcount
    return count

