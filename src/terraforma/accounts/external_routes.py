"""Signing in with an external provider (Google, Microsoft, Facebook, Steam, ...): the calls.

The flow (see accounts.oidc for the protocol): the page sends the browser to ``/api/external/<name>/start``, which sends
it on to the provider; the provider sends it back to ``/api/external/<name>/callback``, which always ends by redirecting
to ``/account/?external=<outcome>`` where the account page carries on:

    done      logged in
    twofa     the provider vouched for the player, but the account has two-factor login on: the page asks for a code
    finish    a first sign-in: the page asks for a username, a handle and (optionally) a password; the account is made then
    linked    a provider was added to the logged-in account
    error     it failed; ``reason`` says why in a word (the page words it)

Steam (kind "steam") brings no email address, so a Steam sign-in with nothing linked ends in ``error`` with ``reason=no_account``:
Steam is for signing in to an account the player already has, linked from the account page.

The rules (plan doc, "Sign-in"): every login has a local account; a provider's email is used only when it says it is
verified; an email that already has an account never creates or links anything, and the answer does not say how that account
signs in; linking is an explicit action by a logged-in player; unlinking needs a fresh two-factor check and never removes the
last way in; two-factor login applies to every way in.

The callback is a GET the provider's redirect makes, so it takes whatever extra parameters the provider adds; everything else
here is a JSON call like the rest of the account calls.
"""

import secrets
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..api.deps import ActingAccount, CurrentAccount, Db, client_address, current_account, sign_in_of
from ..api.security import start_session
from ..models import Account
from ..settings import ExternalProvider
from .tokens import TokenError
from . import oidc, ratelimit, remember, second, service, twofa
from .common import Strict, limited
from .routes import single_method_note

router = APIRouter(prefix="/api/external")

# What the session cookie remembers between calls.
SESSION_FLOW = "external_flow"  # a sign-in under way (accounts.oidc.Flow)
SESSION_SIGNUP = "external_signup"  # a first sign-in waiting for the player to choose a username and handle
SESSION_TWOFA = "external_2fa"  # a sign-in waiting for the two-factor code

# The pages a player can be sent back to after logging in: names, never addresses (the account page knows them).
# The signed request is kept in the session cookie while the player is at the provider, so it is kept short of the cookie's 4 KB.
OIDC_REQUEST_MAX = 2048
NEXT = Literal["play", "profile", "oidc"]


class StartQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    next: NEXT | None = None
    # With ``next=oidc``: the signed request the sign-in provider (``/oidc/authorize``) left in the login page's address, so a store
    # sign-in through an external provider comes back to the store. It is checked here and again at ``/oidc/resume``, never followed.
    request: str | None = Field(default=None, min_length=1, max_length=OIDC_REQUEST_MAX)

    @model_validator(mode="after")
    def _request_goes_with_oidc(self) -> "StartQuery":
        if (self.next == "oidc") != (self.request is not None):
            raise ValueError("request goes with next=oidc, and only with it")
        return self


class CallbackQuery(BaseModel):
    """What the provider adds to the redirect. Providers add more (iss, scope, authuser, ...): those are ignored."""

    model_config = ConfigDict(extra="ignore")

    code: str | None = Field(default=None, max_length=4096)
    state: str | None = Field(default=None, max_length=512)
    error: str | None = Field(default=None, max_length=200)


class FinishRequest(Strict):
    username: str = Field(min_length=1, max_length=32)
    handle: str = Field(min_length=1, max_length=24)
    # Optional: without one the player signs in only through the provider (and may set a password later).
    password: str | None = Field(default=None, max_length=service.PASSWORD_MAX)


class TwoFactorRequest(second.Proof):
    # Remember this browser, so it skips the second factor for the number of days the game sets.
    remember: bool = False


class LinkRequest(second.Proof):
    # (code or assertion: the second factor, required when two-factor login is on)
    pass


class UnlinkRequest(second.Proof):
    provider: str = Field(min_length=1, max_length=32)


class PasswordRequest(second.Proof):
    password: str = Field(max_length=service.PASSWORD_MAX)


def provider_of(request: Request, name: str) -> ExternalProvider:
    for provider in request.app.state.settings.external_providers:
        if provider.name == name:
            return provider
    raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sign-in provider")


def label_of(request: Request, name: str) -> str:
    for provider in request.app.state.settings.external_providers:
        if provider.name == name:
            return provider.label
    return name


def back(outcome: str, reason: str | None = None, next: str | None = None, request: str | None = None) -> RedirectResponse:
    """Ends a browser trip: to the account page, which says what came of it (the address carries a word, never a secret)."""
    query = {"external": outcome}
    if reason:
        query["reason"] = reason
    if next:
        query["next"] = next
        if request:
            query["request"] = request
    return RedirectResponse(f"/account/?{urlencode(query)}", status_code=status.HTTP_303_SEE_OTHER)


def read_flow(request: Request) -> oidc.Flow | None:
    """Takes the sign-in under way out of the session (it works once)."""
    stored = request.session.pop(SESSION_FLOW, None)
    try:
        return oidc.Flow(**stored) if isinstance(stored, dict) else None
    except TypeError:
        return None


async def require_code(request: Request, db: Db, account: Account, proof: second.Proof, what: str) -> None:
    """With two-factor login on, $what needs a live second factor (a code, a recovery code or a security key) as well."""
    if twofa.enabled(account) and not await second.verify(request, db, account, proof):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"a current two-factor code is needed to {what}")


@router.get("/providers")
async def providers(request: Request) -> dict:
    """The providers a player can sign in with (for the buttons); nothing about how they are set up."""
    return {"providers": [{"name": each.name, "label": each.label} for each in request.app.state.settings.external_providers]}


@router.get("/{name}/start")
async def start(name: str, query: Annotated[StartQuery, Query()], request: Request) -> RedirectResponse:
    """Sends the browser to the provider to sign in (or up)."""
    provider = provider_of(request, name)
    await limited(request, ratelimit.EXTERNAL_START_BY_ADDRESS, client_address(request))
    if query.request is not None:
        try:
            request.app.state.tokens.read("oidc-authorize", query.request)
        except TokenError:
            return back("error", "expired")
    flow = oidc.new_flow(provider, "login", next=query.next, request=query.request)
    try:
        where = await oidc.endpoints(request.app.state.http_client, provider)
    except oidc.SignInError:
        return back("error", "failed")
    request.session[SESSION_FLOW] = flow.dump()
    address = oidc.authorize_address(provider, where, request.app.state.settings.public_url, flow)
    return RedirectResponse(address, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/{name}/link")
async def link(name: str, body: LinkRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Begins adding a provider to the logged-in account: answers the address to send the browser to."""
    provider = provider_of(request, name)
    await limited(request, ratelimit.SIGN_IN_METHOD_BY_ACCOUNT, str(account.id))
    await require_code(request, db, account, body, "link a sign-in provider")
    if any(each.provider == name for each in await service.identities_of(db, account)):
        raise HTTPException(status.HTTP_409_CONFLICT, "this account is already linked to that provider")
    flow = oidc.new_flow(provider, "link", account_id=account.id)
    try:
        where = await oidc.endpoints(request.app.state.http_client, provider)
    except oidc.SignInError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "the provider could not be reached") from error
    request.session[SESSION_FLOW] = flow.dump()
    return {"url": oidc.authorize_address(provider, where, request.app.state.settings.public_url, flow)}


@router.get("/{name}/callback")
async def callback(name: str, query: Annotated[CallbackQuery, Query()], request: Request, db: Db) -> RedirectResponse:
    """The provider sends the browser back here with a code. Whatever happens, the browser ends up on the account page."""
    provider = provider_of(request, name)
    await limited(request, ratelimit.EXTERNAL_CALLBACK_BY_ADDRESS, client_address(request))
    flow = read_flow(request)
    if flow is None or flow.provider != name or query.state is None or not secrets.compare_digest(flow.state, query.state):
        return back("error", "expired")
    if query.error is not None:
        return back("error", "denied" if query.error == "access_denied" else "failed")
    if provider.kind != "steam" and query.code is None:
        return back("error", "failed")
    try:
        http = request.app.state.http_client
        public_url = request.app.state.settings.public_url
        if provider.kind == "steam":
            # Steam brings its answer as openid.* values, not a code (and no "error": a cancel is an answer too).
            answer = {key: value for key, value in request.query_params.items() if key.startswith("openid.")}
            who = await oidc.complete_steam(http, provider, public_url, flow, answer)
        else:
            where = await oidc.endpoints(http, provider)
            who = await oidc.complete(http, provider, where, public_url, flow, query.code)
    except oidc.SignInError as error:
        return back("error", error.reason)

    known = await service.find_identity(db, name, who.subject)
    if flow.purpose == "link":
        return await _linked(request, db, flow, name, who, known)
    if known is not None:
        account = await db.get(Account, known.account_id)
        if account is None:
            return back("error", "failed")
        return await _logged_in(request, db, account, flow.next, flow.request)

    if provider.kind == "steam":
        # Steam gives no email address, so it cannot start an account: a player links it to the one they have.
        return back("error", "no_account")
    # A first sign-in with this provider: it needs an email the provider has verified, that no account has.
    try:
        email = service.check_email(who.email) if who.email_verified else ""
    except service.AccountError:
        email = ""
    if not email:
        return back("error", "email_unverified")
    if await service.find_by_email(db, email) is not None:
        return back("error", "email_in_use")
    request.session[SESSION_SIGNUP] = {"provider": name, "subject": who.subject, "email": email, "next": flow.next, "request": flow.request}
    return back("finish", next=flow.next, request=flow.request)


async def _logged_in(request: Request, db: Db, account: Account, next: str | None, oidc_request: str | None = None) -> RedirectResponse:
    """Logs $account in, or, with two-factor login on (and this browser not remembered), waits for the second factor."""
    if twofa.enabled(account) and not await remember.is_remembered(request, db, account):
        request.session.clear()
        request.session[SESSION_TWOFA] = {"account_id": account.id, "version": account.session_version, "next": next, "request": oidc_request}
        return back("twofa", next=next, request=oidc_request)
    start_session(request, account.id, account.session_version)
    return back("done", next=next, request=oidc_request)


async def _linked(request: Request, db: Db, flow: oidc.Flow, name: str, who: oidc.Identity, known) -> RedirectResponse:
    try:
        account = await current_account(request, db)
    except HTTPException:
        return back("error", "expired")
    if account.id != flow.account_id:
        return back("error", "expired")
    if known is not None:
        return back("linked") if known.account_id == account.id else back("error", "taken")
    if any(each.provider == name for each in await service.identities_of(db, account)):
        return back("error", "taken")
    service.link_identity(db, account, name, who.subject, who.email if who.email_verified else "")
    return back("linked")


@router.get("/pending")
async def pending(request: Request) -> dict:
    """For the finish page: the first sign-in waiting for a username and handle, or null."""
    signup = request.session.get(SESSION_SIGNUP)
    if not isinstance(signup, dict):
        return {"pending": None}
    return {"pending": {"provider": label_of(request, str(signup.get("provider"))), "email": signup.get("email")}}


@router.post("/finish")
async def finish(body: FinishRequest, request: Request, db: Db) -> dict:
    """Makes the account of a first sign-in, with the username and handle the player chose."""
    await limited(request, ratelimit.EXTERNAL_FINISH_BY_ADDRESS, client_address(request))
    signup = request.session.get(SESSION_SIGNUP)
    if not isinstance(signup, dict):
        raise HTTPException(status.HTTP_409_CONFLICT, "sign in with a provider first")
    try:
        account = await service.create_external_account(
            db,
            provider=str(signup["provider"]),
            subject=str(signup["subject"]),
            email=str(signup["email"]),
            username=body.username,
            handle=body.handle,
            password=body.password,
        )
    except (service.UsernameTaken, service.HandleTaken) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.EmailTaken as error:
        # Someone registered the address while the player was choosing: start over (the page says to sign in the other way).
        request.session.pop(SESSION_SIGNUP, None)
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    token = start_session(request, account.id, account.session_version)
    return {"username": account.username, "csrf_token": token, "next": signup.get("next")}


async def _waiting(request: Request, db: Db) -> tuple[Account, dict]:
    """The account whose provider sign-in is waiting for the second factor, and what the session kept about it."""
    waiting = request.session.get(SESSION_TWOFA)
    account = await db.get(Account, waiting["account_id"]) if isinstance(waiting, dict) else None
    if account is None or account.session_version != waiting.get("version"):
        raise HTTPException(status.HTTP_409_CONFLICT, "sign in with a provider first")
    return account, waiting


@router.get("/2fa")
async def two_factor_options(request: Request, db: Db) -> dict:
    """For the second step of a provider sign-in: which methods count, and the options a security key is asked with."""
    account, _ = await _waiting(request, db)
    return await second.offer(request, db, account)


@router.post("/2fa")
async def two_factor(body: TwoFactorRequest, request: Request, response: Response, db: Db) -> dict:
    """The second step of a provider sign-in for an account with two-factor login on."""
    account, waiting = await _waiting(request, db)
    if not await second.verify(request, db, account, body):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "that code is wrong or already used")
    if body.remember:
        await remember.remember(request, response, db, account, sign_in_of(request).remember_browser_days)
    note = single_method_note(request, account)
    token = start_session(request, account.id, account.session_version)
    return {"username": account.username, "csrf_token": token, "next": waiting.get("next"), "note": note}


@router.get("/identities")
async def identities(request: Request, account: CurrentAccount, db: Db) -> dict:
    """The providers linked to the logged-in account, and whether it has a password of its own."""
    return {
        "identities": [
            {"provider": each.provider, "label": label_of(request, each.provider), "email": each.email}
            for each in await service.identities_of(db, account)
        ],
        "has_password": bool(account.password_hash),
    }


@router.post("/unlink")
async def unlink(body: UnlinkRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """Removes a linked provider. Needs a fresh two-factor check, and never removes the last way in."""
    await limited(request, ratelimit.SIGN_IN_METHOD_BY_ACCOUNT, str(account.id))
    await require_code(request, db, account, body, "unlink a sign-in provider")
    linked = await service.identities_of(db, account)
    target = next((each for each in linked if each.provider == body.provider), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that provider is not linked to this account")
    if await service.ways_in(db, account) <= 1:
        raise HTTPException(status.HTTP_409_CONFLICT, "this is your only way to log in: set a password first")
    await db.delete(target)
    return {"ok": True}


@router.post("/password")
async def set_password(body: PasswordRequest, request: Request, account: ActingAccount, db: Db) -> dict:
    """The first local password of an account that signs in through a provider."""
    await limited(request, ratelimit.SIGN_IN_METHOD_BY_ACCOUNT, str(account.id))
    if account.password_hash:
        raise HTTPException(status.HTTP_409_CONFLICT, "this account already has a password: use the reset link to change it")
    await require_code(request, db, account, body, "set a password")
    try:
        await service.set_first_password(db, account, body.password)
    except service.AccountError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return {"ok": True}
