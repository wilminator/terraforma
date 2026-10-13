"""Proving a second factor in a call: an app code or recovery code, or a security key's answer.

Every call that asks for one (log in, change the email address, change two-factor login, unlink a provider...) takes the
same two optional fields next to its own: ``code`` and ``assertion`` (what ``navigator.credentials.get`` returned, as JSON
text). A call that wants a key's answer first needs a challenge: ``offer`` makes one and keeps it in the session, and the
answer is checked against it once (reading the challenge takes it out, right or wrong).

Every try counts against the account's rate limit (``TWOFA_CODE_BY_ACCOUNT``), whichever way it is made.
"""

import json

from fastapi import Request
from pydantic import Field, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

from .. import wallclock
from ..api.deps import sign_in_of
from ..models import Account, WebauthnKey
from . import securitykeys, twofa, ratelimit
from .common import Strict, limited

# Where the session keeps a challenge, by what it is for: a registration's must not pass as a login's.
SESSION_CHALLENGE = {"login": "webauthn_challenge", "register": "webauthn_registration"}
#: How long a challenge may wait for the answer, in seconds (a little longer than the browser is told to wait).
CHALLENGE_LIFETIME = 300
ASSERTION_MAX = 16384


class Proof(Strict):
    """The fields a call takes to prove a second factor. A call that needs one extends this."""

    # An app code or a recovery code.
    code: str | None = Field(default=None, min_length=1, max_length=32)
    # What a security key answered (JSON text of the credential).
    assertion: str | None = Field(default=None, min_length=2, max_length=ASSERTION_MAX)

    @model_validator(mode="after")
    def _one_way_only(self):
        if self.code is not None and self.assertion is not None:
            raise ValueError("send a code or a security key's answer, not both")
        return self


def party(request: Request) -> securitykeys.RelyingParty:
    return securitykeys.relying_party(request.app.state.settings.public_url)


async def keys_of(db: AsyncSession, account: Account, *, confirmed: bool = True) -> list[WebauthnKey]:
    query = select(WebauthnKey).where(WebauthnKey.account_id == account.id).order_by(WebauthnKey.id)
    query = query.where(WebauthnKey.confirmed_at.is_not(None) if confirmed else WebauthnKey.confirmed_at.is_(None))
    return list((await db.scalars(query)).all())


def remember_challenge(request: Request, account: Account, challenge: bytes, kind: str = "login") -> None:
    request.session[SESSION_CHALLENGE[kind]] = {
        "account_id": account.id,
        "challenge": bytes_to_base64url(challenge),
        "at": wallclock.timestamp(),
    }


def take_challenge(request: Request, account: Account, kind: str = "login") -> bytes | None:
    """The challenge made for $account, taken out of the session (it works once); None if there is none or it ran out."""
    stored = request.session.pop(SESSION_CHALLENGE[kind], None)
    if not isinstance(stored, dict) or stored.get("account_id") != account.id:
        return None
    if wallclock.timestamp() - int(stored.get("at", 0)) > CHALLENGE_LIFETIME:
        return None
    try:
        return base64url_to_bytes(str(stored["challenge"]))
    except (KeyError, ValueError):
        return None


async def offer(request: Request, db: AsyncSession, account: Account) -> dict:
    """What a page needs to ask for the second factor: the methods that count and, if the account has keys, the options for
    ``navigator.credentials.get`` (a challenge is made and kept for the answer), and for how many days the game lets a browser
    be remembered."""
    keys = await keys_of(db, account)
    options = None
    if keys:
        text, challenge = securitykeys.authentication_options(party(request), keys)
        remember_challenge(request, account, challenge)
        options = json.loads(text)
    return {"methods": twofa.methods(account), "webauthn": options, "remember_days": sign_in_of(request).remember_browser_days}


async def verify(request: Request, db: AsyncSession, account: Account, proof: Proof, *, setting_up: bool = False) -> bool:
    """Whether $proof is a live second factor of $account. Counts a try. A code or recovery code is used up; a key's counter
    and last use are recorded. ($setting_up lets the app's first code through, for the call that turns the app on.)"""
    await limited(request, ratelimit.TWOFA_CODE_BY_ACCOUNT, str(account.id))
    if proof.assertion is not None:
        challenge = take_challenge(request, account)
        credential_id = securitykeys.credential_id_of(proof.assertion)
        if challenge is None or credential_id is None:
            return False
        key = next((each for each in await keys_of(db, account) if each.credential_id == credential_id), None)
        if key is None:
            return False
        try:
            key.sign_count = securitykeys.verify_authentication(party(request), proof.assertion, challenge, key)
        except securitykeys.SecurityKeyError:
            return False
        key.last_used_at = wallclock.now()
        return True
    if proof.code is not None:
        return twofa.check_code(request.app.state.keys, account, proof.code, setting_up=setting_up)
    return False
