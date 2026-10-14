"""The engine as a closed OpenID Connect provider, for the game's store (and any other site listed in settings.toml).

Only the sites listed in ``oidc_clients`` can use it, each with its own secret and exact redirect addresses; with none listed
every address here answers 404. The standard flow is the authorization code flow with PKCE (S256) and nothing else: no implicit
flow, no refresh tokens, no dynamic registration, no consent screen (the sites are the game maker's own). A site learns an
opaque subject id and, with the ``profile`` scope, the player's public handle; never the username or the email address.

These addresses are the standard's, not the game's JSON calls, so they are laid out as the standard has them. The player's
browser only navigates (``authorize``, ``resume``) and never changes anything but a code that only the listed site can trade;
the site's own server makes the rest (``token`` with its secret, ``userinfo`` with the access token), so neither takes a login or
a CSRF token. Every one is rate limited per address. Logs are scrubbed of codes, secrets and tokens.
"""

import base64
from typing import Annotated
from urllib.parse import parse_qsl, unquote_plus

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..accounts.tokens import TokenError
from ..api.deps import Db, client_address, current_account
from ..models import Account
from . import service, signing

router = APIRouter()

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}
# The answers to the browser carry a code in the address: nothing may be sent on to the next page in a Referer header.
BROWSER = {**NO_STORE, "Referrer-Policy": "no-referrer"}
MAX_FORM_BYTES = 16 * 1024


class AuthorizeQuery(BaseModel):
    """What the site put in the address. Extra parameters (``ui_locales``, ``login_hint``...) are ignored, as the standard says."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    response_type: str = Field(default="", max_length=32)
    client_id: str = Field(default="", max_length=64)
    redirect_uri: str = Field(default="", max_length=2048)
    scope: str = Field(default="", max_length=256)
    state: str | None = Field(default=None, max_length=512)
    nonce: str | None = Field(default=None, max_length=256)
    code_challenge: str = Field(default="", max_length=128)
    code_challenge_method: str = Field(default="", max_length=16)
    prompt: str = Field(default="", max_length=64)


class ResumeQuery(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    request: str = Field(min_length=1, max_length=8192)


class TokenForm(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    grant_type: str = Field(default="", max_length=64)
    code: str = Field(default="", max_length=256)
    redirect_uri: str = Field(default="", max_length=2048)
    code_verifier: str = Field(default="", max_length=256)
    client_id: str | None = Field(default=None, max_length=64)
    client_secret: str | None = Field(default=None, max_length=256)


def on(request: Request) -> None:
    """404 unless the game lists at least one site: a game that does not use the provider exposes nothing."""
    if not request.app.state.settings.oidc_clients:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")


def oauth_error(error: str, description: str, code: int = status.HTTP_400_BAD_REQUEST, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": error, "error_description": description}, status_code=code, headers={**NO_STORE, **(headers or {})})


# --- the keys and the discovery document -----------------------------------------------------------------------------------

@router.get("/.well-known/openid-configuration")
async def discovery(request: Request) -> JSONResponse:
    on(request)
    base = service.issuer(request.app.state.settings)
    document = {
        "issuer": base,
        **{name: base + path for name, path in service.ENDPOINTS.items()},
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code"],
        "subject_types_supported": ["pairwise"],
        "id_token_signing_alg_values_supported": [signing.ALGORITHM],
        "scopes_supported": list(service.SCOPES),
        "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        "code_challenge_methods_supported": ["S256"],
        "claims_supported": ["sub", "iss", "aud", "exp", "iat", "nonce", "preferred_username"],
        "prompt_values_supported": ["none", "login"],
        "authorization_response_iss_parameter_supported": True,
    }
    return JSONResponse(document, headers={"Cache-Control": "public, max-age=300"})


@router.get("/oidc/jwks")
async def jwks(request: Request) -> JSONResponse:
    """The public signing keys: the current one and, after a rotation, the previous one."""
    on(request)
    return JSONResponse(signing.key_set(request.app.state.keys), headers={"Cache-Control": "public, max-age=300"})


# --- the player's browser: authorize and resume ---------------------------------------------------------------------------------

def go_back(ask: dict, error: str, description: str | None = None) -> RedirectResponse:
    """Sends the player back to the site with an error, as the standard has it."""
    return RedirectResponse(
        service.with_parameters(ask["redirect_uri"], error=error, error_description=description, state=ask.get("state"), iss=ask["iss"]),
        status_code=status.HTTP_303_SEE_OTHER,
        headers=BROWSER,
    )


async def logged_in(request: Request, db: Db) -> Account | None:
    try:
        return await current_account(request, db)
    except HTTPException:
        return None


async def proceed(request: Request, db: Db, ask: dict, *, resumed: bool = False) -> Response:
    """The steps of an authorization request. $ask holds what the site asked for; a resumed one has come back from the login."""
    settings = request.app.state.settings
    # Until the site and the address are known to be right, nothing may be sent to the address: the player sees the problem.
    client = service.find_client(settings, ask["client_id"])
    if client is None:
        return oauth_error("invalid_request", "client_id is not a site this game signs players in for")
    if ask["redirect_uri"] not in client.redirect_uris:
        return oauth_error("invalid_request", "redirect_uri is not one of the addresses listed for this site")
    ask = {**ask, "iss": service.issuer(settings)}
    if ask["response_type"] != "code":
        return go_back(ask, "unsupported_response_type", "only response_type=code is supported")
    scope = service.granted_scope(ask["scope"])
    if scope is None:
        return go_back(ask, "invalid_scope", "scope must include openid")
    if not service.CHALLENGE.fullmatch(ask["code_challenge"]) or ask["code_challenge_method"] != "S256":
        return go_back(ask, "invalid_request", "PKCE is required: send code_challenge (S256) and code_challenge_method=S256")
    prompts = ask["prompt"].split()
    if not set(prompts) <= {"none", "login", "consent", "select_account"} or ("none" in prompts and len(prompts) > 1):
        return go_back(ask, "invalid_request", "prompt is none or login (consent and select_account change nothing here)")
    if resumed:
        prompts = []
    account = await logged_in(request, db)
    if "login" in prompts and account is not None:
        request.session.clear()  # the player signs in again; the request waits in the address
        account = None
    if account is None:
        if "none" in prompts:
            return go_back(ask, "login_required", "the player is not signed in")
        waiting = {name: value for name, value in ask.items() if name not in {"iss", "prompt"}}
        link = request.app.state.tokens.make("oidc-authorize", 0, **waiting)
        return RedirectResponse(f"/account/?next=oidc&request={link}", status_code=status.HTTP_303_SEE_OTHER, headers=BROWSER)
    if not account.handle:
        return go_back(ask, "access_denied", "the player has not chosen a public handle yet: they can on the account page")
    code = await service.make_code(db, account, client, ask["redirect_uri"], scope, ask["code_challenge"], ask["nonce"] or None)
    return RedirectResponse(
        service.with_parameters(ask["redirect_uri"], code=code, state=ask["state"], iss=ask["iss"]),
        status_code=status.HTTP_303_SEE_OTHER,
        headers=BROWSER,
    )


@router.get("/oidc/authorize")
async def authorize(request: Request, query: Annotated[AuthorizeQuery, Query()], db: Db) -> Response:
    """The site sends the player here. A player who is signed in is sent straight back with a code (there is no consent screen:
    the sites are the game maker's own); one who is not goes to the login first and comes back."""
    on(request)
    await limited(request, ratelimit.OIDC_AUTHORIZE_BY_ADDRESS, client_address(request))
    names = [name for name, _ in request.query_params.multi_items()]
    if len(names) != len(set(names)):
        return oauth_error("invalid_request", "a parameter was sent more than once")
    return await proceed(request, db, query.model_dump())


@router.get("/oidc/resume")
async def resume(request: Request, query: Annotated[ResumeQuery, Query()], db: Db) -> Response:
    """Where the login page sends the player back to: the request that was waiting for them, from the address."""
    on(request)
    await limited(request, ratelimit.OIDC_AUTHORIZE_BY_ADDRESS, client_address(request))
    try:
        waiting = request.app.state.tokens.read("oidc-authorize", query.request).data
        ask = AuthorizeQuery.model_validate(waiting).model_dump()
    except (TokenError, ValidationError):
        return oauth_error("invalid_request", "this sign-in took too long or is not valid: start again from the site")
    return await proceed(request, db, ask, resumed=True)


# --- the site's server: token and userinfo ----------------------------------------------------------------------------------------

async def read_form(request: Request) -> TokenForm | JSONResponse:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        return oauth_error("invalid_request", "send the call as application/x-www-form-urlencoded")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_FORM_BYTES:
            return oauth_error("invalid_request", "the call is too large")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return oauth_error("invalid_request", "the call is not UTF-8")
    names = [name for name, _ in pairs]
    if len(names) != len(set(names)):
        return oauth_error("invalid_request", "a parameter was sent more than once")
    try:
        return TokenForm.model_validate(dict(pairs))
    except ValidationError:
        return oauth_error("invalid_request", "a parameter is too long")


def basic_credentials(authorization: str | None) -> tuple[str, str] | None:
    """The client id and secret in an ``Authorization: Basic`` header (RFC 6749, 2.3.1: each form-encoded), or None."""
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        identifier, separator, secret = base64.b64decode(given.strip(), validate=True).decode("utf-8").partition(":")
    except ValueError:
        return "", ""
    return (unquote_plus(identifier), unquote_plus(secret)) if separator else ("", "")


def refuse_client(basic: bool) -> JSONResponse:
    return oauth_error("invalid_client", "the client id or secret is wrong", status.HTTP_401_UNAUTHORIZED, {"WWW-Authenticate": 'Basic realm="oidc"'} if basic else None)


@router.post("/oidc/token")
async def token(request: Request, db: Db, authorization: Annotated[str | None, Header()] = None) -> Response:
    """The site's server trades a code for the ID token and an access token. Sign in with ``client_secret_basic`` or
    ``client_secret_post``. Errors are the standard's: a JSON body with ``error`` and ``error_description``."""
    on(request)
    await limited(request, ratelimit.OIDC_TOKEN_BY_ADDRESS, client_address(request))
    form = await read_form(request)
    if isinstance(form, JSONResponse):
        return form
    basic = basic_credentials(authorization)
    if basic is not None and (form.client_id is not None or form.client_secret is not None):
        return oauth_error("invalid_request", "send the client's credentials one way, not both")
    identifier, secret = basic if basic is not None else (form.client_id or "", form.client_secret or "")
    client = service.find_client(request.app.state.settings, identifier)
    if not service.check_secret(client, secret):
        return refuse_client(basic is not None)
    if form.grant_type != "authorization_code":
        return oauth_error("unsupported_grant_type", "only grant_type=authorization_code is supported")
    if not (form.code and form.redirect_uri and form.code_verifier):
        return oauth_error("invalid_request", "code, redirect_uri and code_verifier are all needed")
    done = await service.trade_code(db, request.app.state.keys, request.app.state.settings, client, form.code, form.redirect_uri, form.code_verifier)
    if isinstance(done, service.Refused):
        return oauth_error(done.error, done.description)  # returned, not raised: a replayed code's revocation must be kept
    return JSONResponse(
        {"access_token": done.access_token, "token_type": "Bearer", "expires_in": done.expires_in, "id_token": done.id_token, "scope": done.scope},
        headers=NO_STORE,
    )


def refuse_token(error: str | None = None) -> JSONResponse:
    header = 'Bearer error="invalid_token"' if error else "Bearer"
    return JSONResponse({"error": error or "invalid_request"}, status_code=status.HTTP_401_UNAUTHORIZED, headers={**NO_STORE, "WWW-Authenticate": header})


@router.get("/oidc/userinfo")
@router.post("/oidc/userinfo")
async def userinfo(request: Request, db: Db, authorization: Annotated[str | None, Header()] = None) -> Response:
    """What the access token allows: the subject id and, with the ``profile`` scope, the public handle as ``preferred_username``."""
    on(request)
    await limited(request, ratelimit.OIDC_USERINFO_BY_ADDRESS, client_address(request))
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not given.strip():
        return refuse_token()
    claims = await service.userinfo(db, given.strip())
    if claims is None:
        return refuse_token("invalid_token")
    return JSONResponse(claims, headers=NO_STORE)
