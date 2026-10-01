"""The 2FA calls: set up, turn on, turn off, new recovery codes, and the emailed link that confirms each.

Setup hands out a secret that does nothing yet. Turning on, turning off and
new recovery codes each need a live code (or a recovery code) and are then
confirmed by a link mailed to the account's address, followed while logged in.
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from .. import wallclock
from ..api.deps import ActingAccount, CurrentAccount, Db
from ..mail import Mail
from ..models import Account
from . import ratelimit, twofa
from .routes import ACCEPTED, Strict, game_name, limited, link, send
from .service import AccountError
from .tokens import TokenError

router = APIRouter(prefix="/api/2fa")


class CodeRequest(Strict):
    code: str = Field(min_length=1, max_length=32)


class ConfirmRequest(Strict):
    token: str = Field(min_length=1, max_length=512)


@router.get("")
async def status_of(account: CurrentAccount) -> dict:
    return {"enabled": twofa.enabled(account), "recovery_codes_left": len(account.recovery_codes or [])}


@router.post("/setup")
async def setup(request: Request, account: ActingAccount) -> dict:
    """A new secret for the authenticator app (as text and as the QR code's address). It counts only once turned on."""
    await limited(request, ratelimit.TWOFA_CHANGE_BY_ACCOUNT, str(account.id))
    try:
        secret = twofa.start_setup(request.app.state.keys, account)
    except AccountError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {"secret": secret, "uri": twofa.uri(secret, account.username, game_name(request))}


async def _proved(request: Request, account: Account, code: str) -> None:
    """Refuses unless $code is live: the app is set up, or it's a recovery code."""
    await limited(request, ratelimit.TWOFA_CODE_BY_ACCOUNT, str(account.id))
    if not twofa.check_code(request.app.state.keys, account, code):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "that code is wrong or already used")


async def _mail_link(request: Request, account: Account, action: str, what: str) -> None:
    await limited(request, ratelimit.TWOFA_CHANGE_BY_ACCOUNT, str(account.id))
    nonce = twofa.open_change(account)
    token = request.app.state.tokens.make("twofa-change", account.id, action=action, nonce=nonce)
    name = game_name(request)
    await send(request, Mail(
        to=account.email,
        subject=f"{name}: confirm {what}",
        body=f"{account.username}, you asked to {what}.\n\nTo confirm, open this while logged in:\n\n"
             f"{link(request, '/confirm-2fa', token)}\n\nThe link works for an hour, once. "
             "If you didn't ask, ignore this and consider resetting your password.\n",
    ))


@router.post("/enable", status_code=status.HTTP_202_ACCEPTED)
async def enable(body: CodeRequest, request: Request, account: ActingAccount) -> dict:
    """Proves the app shows the right code, then mails the link that turns 2FA on."""
    if twofa.enabled(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is already on")
    if account.totp_secret is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "set it up first")
    await _proved(request, account, body.code)
    await _mail_link(request, account, "enable", "turn on two-factor login")
    return ACCEPTED


@router.post("/disable", status_code=status.HTTP_202_ACCEPTED)
async def disable(body: CodeRequest, request: Request, account: ActingAccount) -> dict:
    if not twofa.enabled(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is not on")
    await _proved(request, account, body.code)
    await _mail_link(request, account, "disable", "turn off two-factor login")
    return ACCEPTED


@router.post("/recovery-codes", status_code=status.HTTP_202_ACCEPTED)
async def recovery_codes(body: CodeRequest, request: Request, account: ActingAccount) -> dict:
    if not twofa.enabled(account):
        raise HTTPException(status.HTTP_409_CONFLICT, "two-factor login is not on")
    await _proved(request, account, body.code)
    await _mail_link(request, account, "recovery", "get new recovery codes")
    return ACCEPTED


@router.post("/confirm")
async def confirm(body: ConfirmRequest, request: Request, account: ActingAccount) -> dict:
    """Following the emailed link, logged in as the account it was made for. Works once."""
    try:
        token = request.app.state.tokens.read("twofa-change", body.token)
    except TokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    action = str(token.data.get("action", ""))
    if token.account_id != account.id or not twofa.close_change(account, str(token.data.get("nonce", ""))):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
    if action == "enable" and account.totp_secret is not None and not twofa.enabled(account):
        account.totp_enabled_at = wallclock.now()
        return {"enabled": True, "recovery_codes": twofa.set_recovery_codes(account)}
    if action == "disable" and twofa.enabled(account):
        account.totp_secret = account.totp_enabled_at = account.totp_last_step = account.recovery_codes = None
        return {"enabled": False}
    if action == "recovery" and twofa.enabled(account):
        return {"enabled": True, "recovery_codes": twofa.set_recovery_codes(account)}
    raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
