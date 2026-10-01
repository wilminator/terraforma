"""The account calls: register, confirm email, log in (with a 2FA code when it's on) and out, reset a password,
change the email address, set a handle.

Calls made before logging in can't carry the CSRF token, but they only
accept a JSON body, which another site's page can't send cross-origin
without the browser asking this server first (and it says no).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from ..api.deps import ActingAccount, CurrentAccount, Db, client_address
from ..api.security import start_session
from ..mail import Mail
from . import ratelimit, service, twofa
from .tokens import TokenError

router = APIRouter(prefix="/api")

ACCEPTED = {"ok": True}


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class RegisterRequest(Strict):
    username: str = Field(max_length=32)
    email: str = Field(max_length=254)
    password: str = Field(max_length=service.PASSWORD_MAX)


class TokenRequest(Strict):
    token: str = Field(min_length=1, max_length=512)


class LoginRequest(Strict):
    username: str = Field(min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=service.PASSWORD_MAX)
    # An app code or a recovery code; only for accounts with 2FA on.
    code: str | None = Field(default=None, min_length=1, max_length=32)


class HandleRequest(Strict):
    handle: str = Field(min_length=1, max_length=24)


class EmailChangeRequest(Strict):
    email: str = Field(min_length=3, max_length=254)


class ResetRequest(Strict):
    email: str = Field(min_length=3, max_length=254)


class ResetComplete(Strict):
    token: str = Field(min_length=1, max_length=512)
    password: str = Field(max_length=service.PASSWORD_MAX)


async def limited(request: Request, limit: ratelimit.Limit, value: str) -> None:
    try:
        await ratelimit.hit(request.app.state.sessionmaker, limit, value)
    except ratelimit.RateLimited as error:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, str(error), headers={"Retry-After": str(error.retry_after)}
        ) from error


def link(request: Request, path: str, token: str) -> str:
    return f"{request.app.state.settings.public_url.rstrip('/')}{path}?token={token}"


async def send(request: Request, mail: Mail) -> None:
    await request.app.state.mailer.send(mail)


def game_name(request: Request) -> str:
    game = request.app.state.game
    return game.name if game else "TerraForma"


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
async def register(body: RegisterRequest, request: Request, db: Db) -> dict:
    """A new account; the confirmation link goes to the email address.

    An address that already has an account gets a note saying so instead,
    and the caller sees the same answer either way: registering can't be
    used to find out who plays.
    """
    await limited(request, ratelimit.REGISTER_BY_ADDRESS, client_address(request))
    name = game_name(request)
    try:
        account = await service.create_account(db, body.username, body.password, email=body.email)
    except service.EmailTaken:
        await send(request, Mail(
            to=body.email.strip(),
            subject=f"{name}: someone tried to register with your address",
            body=f"Someone tried to make a new {name} account with this email address, which already has one.\n\n"
                 "If it was you, log in instead (or reset your password). If not, you can ignore this.\n",
        ))
        return ACCEPTED
    except service.UsernameTaken as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    token = request.app.state.tokens.make("confirm-email", account.id, email=account.email)
    await send(request, Mail(
        to=account.email,
        subject=f"{name}: confirm your email address",
        body=f"Welcome to {name}, {account.username}!\n\nConfirm your email address to start playing:\n\n"
             f"{link(request, '/confirm-email', token)}\n\nThe link works for 3 days. If you didn't sign up, ignore this.\n",
    ))
    return ACCEPTED


@router.post("/confirm-email")
async def confirm_email(body: TokenRequest, request: Request, db: Db) -> dict:
    try:
        token = request.app.state.tokens.read("confirm-email", body.token)
    except TokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    account = await service.confirm_email(db, token.account_id, token.data.get("email", ""))
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
    return {"confirmed": True, "username": account.username}


@router.post("/login")
async def login(body: LoginRequest, request: Request, db: Db) -> dict:
    await limited(request, ratelimit.LOGIN_BY_ADDRESS, client_address(request))
    await limited(request, ratelimit.LOGIN_BY_NAME, body.username)
    account = await service.authenticate(db, body.username, body.password)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username or password")
    if account.email_confirmed_at is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "confirm your email address first: the link is in your inbox")
    if twofa.enabled(account):
        if body.code is None:
            # The password was right; now the second factor. No login yet.
            return JSONResponse({"needs_code": True}, status_code=status.HTTP_202_ACCEPTED)
        await limited(request, ratelimit.TWOFA_CODE_BY_ACCOUNT, str(account.id))
        if not twofa.check_code(request.app.state.keys, account, body.code):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "wrong username, password or code")
    token = start_session(request, account.id, account.session_version)
    return {"username": account.username, "csrf_token": token}


@router.post("/logout")
async def logout(request: Request, account: ActingAccount) -> dict:
    request.session.clear()
    return ACCEPTED


@router.get("/me")
async def me(account: CurrentAccount) -> dict:
    return {"username": account.username, "handle": account.handle}


@router.post("/handle")
async def set_handle(body: HandleRequest, account: ActingAccount, db: Db) -> dict:
    """Sets the name other players see: never the username or email address."""
    try:
        await service.set_handle(db, account, body.handle)
    except service.HandleTaken as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return {"handle": account.handle}


@router.post("/password-reset", status_code=status.HTTP_202_ACCEPTED)
async def password_reset(body: ResetRequest, request: Request, db: Db) -> dict:
    """Mails a reset link if the address has an account. The answer is the same either way."""
    await limited(request, ratelimit.RESET_BY_ADDRESS, client_address(request))
    await limited(request, ratelimit.RESET_BY_EMAIL, body.email.strip())
    account = await service.find_by_email(db, body.email)
    if account is not None:
        token = request.app.state.tokens.make("password-reset", account.id, version=account.session_version)
        name = game_name(request)
        await send(request, Mail(
            to=account.email,
            subject=f"{name}: reset your password",
            body=f"Someone asked to reset the password for {account.username}.\n\nTo choose a new one:\n\n"
                 f"{link(request, '/reset-password', token)}\n\nThe link works for an hour, once. "
                 "Resetting logs you out everywhere. If you didn't ask, ignore this: your password hasn't changed.\n",
        ))
    return ACCEPTED


@router.post("/password-reset/complete")
async def password_reset_complete(body: ResetComplete, request: Request, db: Db) -> dict:
    try:
        token = request.app.state.tokens.read("password-reset", body.token)
        account = await service.reset_password(db, token.account_id, int(token.data.get("version", -1)), body.password)
    except TokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link has already been used")
    request.session.clear()
    return {"ok": True, "username": account.username}


@router.post("/email-change", status_code=status.HTTP_202_ACCEPTED)
async def email_change(body: EmailChangeRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Mails a link to the NEW address; the change happens when it's followed. Never needs the password.

    The old address is told too, so a hijacked login can't change it silently.
    An address another account has gets a warning instead, and the caller
    sees the same answer either way.
    """
    await limited(request, ratelimit.EMAIL_CHANGE_BY_ACCOUNT, str(account.id))
    try:
        new_email = service.check_email(body.email)
        if account.handle:
            service.check_handle(account.handle, username=account.username, email=new_email)
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    name = game_name(request)
    if service.email_key(new_email) == account.email_key:
        return ACCEPTED
    if not await service.email_available(db, account, new_email):
        await send(request, Mail(
            to=new_email,
            subject=f"{name}: someone tried to use your address",
            body=f"Someone tried to move a {name} account to this email address, which already has one.\n\n"
                 "If it was you, log in to that account instead. If not, you can ignore this.\n",
        ))
        return ACCEPTED
    token = request.app.state.tokens.make("change-email", account.id, old=account.email_key, new=new_email)
    await send(request, Mail(
        to=new_email,
        subject=f"{name}: confirm your new email address",
        body=f"{account.username} asked to use this address for their {name} account.\n\n"
             f"To make the change:\n\n{link(request, '/change-email', token)}\n\n"
             "The link works for an hour, once, and logs the account out everywhere. If you didn't ask, ignore this.\n",
    ))
    await send(request, Mail(
        to=account.email,
        subject=f"{name}: your email address is being changed",
        body=f"A change of the email address on {account.username} was requested, to {new_email}.\n\n"
             "Nothing changes until the link sent to the new address is followed. If this wasn't you, "
             "reset your password: that logs out whoever is in your account.\n",
    ))
    return ACCEPTED


@router.post("/email-change/complete")
async def email_change_complete(body: TokenRequest, request: Request, db: Db) -> dict:
    """Following the link sent to the new address. Works once: the old address must still be the account's."""
    try:
        token = request.app.state.tokens.read("change-email", body.token)
        account = await service.change_email(
            db, token.account_id, str(token.data.get("old", "")), str(token.data.get("new", ""))
        )
    except TokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from error
    except service.EmailTaken as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is no longer valid")
    request.session.clear()
    return {"ok": True, "username": account.username}
