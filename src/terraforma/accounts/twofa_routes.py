"""The 2FA calls: set up and turn on the authenticator app, add and remove security keys (WebAuthn), turn it all off, new
recovery codes, remembered browsers, and the emailed link that confirms each change. Also the admin's reset.

Two-factor login is on while at least one method counts: the app, or a key. Setting up the app hands out a secret that does
nothing yet; registering a key stores it unconfirmed. Turning a method on, removing one, turning it all off and new recovery
codes each need a live second factor (a code, a recovery code or a key's answer; none yet when the first method is being set
up) and are then confirmed by a link mailed to the account's address, followed while logged in. Every confirmed change makes
the browsers that were remembered prove themselves again.
"""

import json
import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..api.deps import ActingAccount, AdminAccount, CurrentAccount, Db
from ..mail import Mail
from ..models import Account, WebauthnKey
from . import ratelimit, remember, second, securitykeys, twofa
from .common import Strict, limited
from .routes import ACCEPTED, game_name, link, send
from .service import AccountError, find_by_username
from .tokens import TokenError

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/2fa")
admin_router = APIRouter(prefix="/api/admin")


class EnableRequest(Strict):
    # What the app shows for the secret just set up.
    code: str = Field(min_length=1, max_length=32)
    # The second factor the account already has, when it has one (a key): turning the app on is a change to it.
    proof: second.Proof | None = None


class RegisterRequest(Strict):
    # What navigator.credentials.create returned, as JSON text.
    credential: str = Field(min_length=2, max_length=second.ASSERTION_MAX)
    name: str | None = Field(default=None, max_length=80)


class RemoveKeyRequest(second.Proof):
    key_id: int = Field(ge=1)


class OptionsRequest(Strict):
    # The second factor the account already has, when it has one: adding a key is a change to it.
    proof: second.Proof | None = None


class ConfirmRequest(Strict):
    token: str = Field(min_length=1, max_length=512)


class ForgetRequest(Strict):
    # One remembered browser; left out: all of them.
    id: int | None = Field(default=None, ge=1)


class AdminResetRequest(Strict):
    username: str = Field(min_length=1, max_length=32)


def moment(value: datetime | None) -> str | None:
    return remember.aware(value).isoformat() if value else None


@router.get("")
async def status_of(account: CurrentAccount, db: Db) -> dict:
    keys = await second.keys_of(db, account)
    return {
        "enabled": twofa.enabled(account),
        "recovery_codes_left": len(account.recovery_codes or []),
        "app": twofa.app_on(account),
        "keys": [
            {"id": key.id, "name": key.name, "added": moment(key.confirmed_at), "last_used": moment(key.last_used_at)} for key in keys
        ],
    }


@router.post("/challenge")
async def challenge(request: Request, account: ActingAccount, db: Db) -> dict:
    """The options for ``navigator.credentials.get``, for a call that wants a security key's answer (and which methods count)."""
    return await second.offer(request, db, account)


@router.post("/setup")
async def setup(request: Request, account: ActingAccount) -> dict:
    """A new secret for the authenticator app (as text and as the QR code's address). It counts only once turned on."""
    await limited(request, ratelimit.TWOFA_CHANGE_BY_ACCOUNT, str(account.id))
    try:
        secret = twofa.start_setup(request.app.state.keys, account)
    except AccountError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {"secret": secret, "uri": twofa.uri(secret, account.username, game_name(request))}


async def _proved(request: Request, db: AsyncSession, account: Account, proof: second.Proof | None, what: str = "make this change") -> None:
    """Refuses unless $proof is a live second factor. (An account with none yet has nothing to prove.)"""
    if not twofa.enabled(account):
        return
    if proof is None or not await second.verify(request, db, account, proof):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"a current two-factor code, recovery code or security key is needed to {what}")


async def _mail_link(request: Request, account: Account, action: str, what: str, *, counted: bool = False, **extra) -> None:
    """Mails the link that confirms a change. ($counted: the caller has already counted it against the rate limit, which has to
    happen before anything is written: the limit is kept in a transaction of its own.)"""
    if not counted:
        await limited(request, ratelimit.TWOFA_CHANGE_BY_ACCOUNT, str(account.id))
    nonce = twofa.open_change(account)
    token = request.app.state.tokens.make("twofa-change", account.id, action=action, nonce=nonce, **extra)
    name = game_name(request)
    await send(request, Mail(
        to=account.email,
        subject=f"{name}: confirm {what}",
        body=f"{account.username}, you asked to {what}.\n\nTo confirm, open this while logged in:\n\n"
             f"{link(request, '/confirm-2fa', token)}\n\nThe link works for an hour, once. "
             "If you didn't ask, ignore this and consider resetting your password.\n",
    ))


@router.post("/enable", status_code=status.HTTP_202_ACCEPTED)
async def enable(body: EnableRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Proves the app shows the right code, then mails the link that turns the app on."""
    if twofa.app_on(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "the authenticator app is already on")
    if account.totp_secret is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "set it up first")
    await limited(request, ratelimit.TWOFA_CODE_BY_ACCOUNT, str(account.id))
    await _proved(request, db, account, body.proof, "add an authenticator app")
    if not twofa.check_setup_code(request.app.state.keys, account, body.code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "that code is wrong or already used")
    await _mail_link(request, account, "enable", "turn on the authenticator app")
    return ACCEPTED


@router.post("/disable", status_code=status.HTTP_202_ACCEPTED)
async def disable(body: second.Proof, request: Request, account: ActingAccount, db: Db) -> dict:
    """Turns off every method (the app and all keys) and the recovery codes."""
    if not twofa.enabled(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is not on")
    await _proved(request, db, account, body, "turn off two-factor login")
    await _mail_link(request, account, "disable", "turn off two-factor login")
    return ACCEPTED


@router.post("/remove-app", status_code=status.HTTP_202_ACCEPTED)
async def remove_app(body: second.Proof, request: Request, account: ActingAccount, db: Db) -> dict:
    """Takes the authenticator app away while a security key stays. (The last method goes with ``disable``.)"""
    if not twofa.app_on(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "the authenticator app is not on")
    if account.webauthn_count < 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "the app is your only second factor: add a security key first, or turn two-factor login off")
    await _proved(request, db, account, body, "remove the authenticator app")
    await _mail_link(request, account, "remove-app", "remove the authenticator app")
    return ACCEPTED


@router.post("/recovery-codes", status_code=status.HTTP_202_ACCEPTED)
async def recovery_codes(body: second.Proof, request: Request, account: ActingAccount, db: Db) -> dict:
    if not twofa.enabled(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is not on")
    await _proved(request, db, account, body, "get new recovery codes")
    await _mail_link(request, account, "recovery", "get new recovery codes")
    return ACCEPTED


# --- security keys --------------------------------------------------------------------------------------------------

@router.post("/webauthn/options")
async def key_options(body: OptionsRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """The options for ``navigator.credentials.create``, to add a security key or passkey. The key counts only once it is
    registered and the emailed link is followed."""
    await limited(request, ratelimit.TWOFA_KEY_BY_ACCOUNT, str(account.id))
    await _proved(request, db, account, body.proof, "add a security key")
    text, challenge = securitykeys.registration_options(
        second.party(request), game_name(request), account, await second.keys_of(db, account)
    )
    second.remember_challenge(request, account, challenge, "register")
    return {"options": json.loads(text)}


@router.post("/webauthn/register", status_code=status.HTTP_202_ACCEPTED)
async def key_register(body: RegisterRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Takes what ``navigator.credentials.create`` returned, keeps the key unconfirmed and mails the link that turns it on."""
    await limited(request, ratelimit.TWOFA_KEY_BY_ACCOUNT, str(account.id))
    expected = second.take_challenge(request, account, "register")
    if expected is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "ask for the security key's options first (they may have run out)")
    try:
        new = securitykeys.verify_registration(second.party(request), body.credential, expected)
    except securitykeys.SecurityKeyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if await db.scalar(select(WebauthnKey.id).where(WebauthnKey.credential_id == new.credential_id)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "that security key is already registered")
    # Counted before anything is written: the limit is kept in a transaction of its own.
    await limited(request, ratelimit.TWOFA_CHANGE_BY_ACCOUNT, str(account.id))
    # Only one registration waits for its link: a newer one replaces it.
    await db.execute(delete(WebauthnKey).where(WebauthnKey.account_id == account.id, WebauthnKey.confirmed_at.is_(None)))
    name = securitykeys.clean_name(body.name)
    key = WebauthnKey(account_id=account.id, credential_id=new.credential_id, public_key=new.public_key, sign_count=new.sign_count, name=name)
    db.add(key)
    await db.flush()
    await _mail_link(request, account, "add-key", f"add the security key \"{name}\"", counted=True, key=key.id)
    return ACCEPTED


@router.post("/webauthn/remove", status_code=status.HTTP_202_ACCEPTED)
async def key_remove(body: RemoveKeyRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Removes a security key while another method stays. (The last method goes with ``disable``.)"""
    key = next((each for each in await second.keys_of(db, account) if each.id == body.key_id), None)
    if key is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such security key")
    if account.webauthn_count < 2 and not twofa.app_on(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "this is your only second factor: add another first, or turn two-factor login off")
    await _proved(request, db, account, body, "remove a security key")
    await _mail_link(request, account, "remove-key", f"remove the security key \"{key.name}\"", key=key.id)
    return ACCEPTED


# --- remembered browsers --------------------------------------------------------------------------------------------

@router.get("/browsers")
async def browsers(request: Request, account: CurrentAccount, db: Db) -> dict:
    """The browsers that may skip the second factor for now."""
    return {"browsers": await remember.listing(db, account, request)}


@router.post("/browsers/forget")
async def forget_browsers(body: ForgetRequest, account: ActingAccount, db: Db) -> dict:
    """Makes one remembered browser, or all of them, prove a second factor again. Needs no proof: it only takes trust away."""
    return {"forgotten": await remember.forget(db, account, body.id)}


# --- the emailed link --------------------------------------------------------------------------------------------------

async def wipe(db: AsyncSession, account: Account) -> None:
    """Every method, the recovery codes and every remembered browser gone: two-factor login is off."""
    twofa.clear_app(account)
    await db.execute(delete(WebauthnKey).where(WebauthnKey.account_id == account.id))
    account.webauthn_count = 0
    account.recovery_codes = None
    account.twofa_change_nonce = None
    await remember.forget(db, account)


@router.post("/confirm")
async def confirm(body: ConfirmRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Following the emailed link, logged in as the account it was made for. Works once."""
    try:
        token = request.app.state.tokens.read("twofa-change", body.token)
    except TokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    action = str(token.data.get("action", ""))
    if token.account_id != account.id or not twofa.close_change(account, str(token.data.get("nonce", ""))):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
    no_longer_valid = HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
    if action == "enable" and account.totp_secret is not None and not twofa.app_on(account):
        account.totp_enabled_at = wallclock.now()
        result = {"enabled": True, "recovery_codes": twofa.first_recovery_codes(account)}
    elif action == "add-key":
        key = await db.get(WebauthnKey, int(token.data.get("key", 0)))
        if key is None or key.account_id != account.id or key.confirmed_at is not None:
            raise no_longer_valid
        key.confirmed_at = wallclock.now()
        account.webauthn_count += 1
        result = {"enabled": True, "recovery_codes": twofa.first_recovery_codes(account)}
    elif action == "disable" and twofa.enabled(account):
        await wipe(db, account)
        return {"enabled": False}
    elif action == "remove-app" and twofa.app_on(account) and account.webauthn_count > 0:
        twofa.clear_app(account)
        result = {"enabled": True, "recovery_codes": None}
    elif action == "remove-key" and twofa.enabled(account):
        key = await db.get(WebauthnKey, int(token.data.get("key", 0)))
        if key is None or key.account_id != account.id or key.confirmed_at is None:
            raise no_longer_valid
        if account.webauthn_count < 2 and not twofa.app_on(account):
            raise no_longer_valid
        await db.delete(key)
        account.webauthn_count -= 1
        result = {"enabled": True, "recovery_codes": None}
    elif action == "recovery" and twofa.enabled(account):
        result = {"enabled": True, "recovery_codes": twofa.set_recovery_codes(account)}
    else:
        raise no_longer_valid
    await remember.forget(db, account)
    return result


# --- the admin's reset -------------------------------------------------------------------------------------------------

@admin_router.post("/2fa-reset")
async def admin_reset(body: AdminResetRequest, request: Request, admin: AdminAccount, db: Db) -> dict:
    """For a player who lost their authenticator and their recovery codes: turns their two-factor login off, logs them out
    everywhere and tells them by email. They log in with their password and set it up again."""
    await limited(request, ratelimit.ADMIN_RESET_BY_ACCOUNT, str(admin.id))
    target = await find_by_username(db, body.username)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such account")
    await wipe(db, target)
    target.session_version += 1
    log.warning("admin account %s reset two-factor login for account %s", admin.id, target.id)
    if target.email:
        name = game_name(request)
        await send(request, Mail(
            to=target.email,
            subject=f"{name}: two-factor login was reset",
            body=f"{target.username}, an administrator turned off two-factor login on your {name} account at your request "
                 "and logged you out everywhere.\n\nLog in with your password, then set two-factor login up again. "
                 "If you did not ask for this, reset your password now.\n",
        ))
    return {"reset": True}
