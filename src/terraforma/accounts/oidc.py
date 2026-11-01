"""Signing in with an OpenID Connect provider: the authorization code flow with PKCE.

The server sends the player's browser to the provider with a one-time ``state``, ``nonce`` and PKCE challenge (kept in the
signed session cookie), and gets the browser back with a ``code``. It trades the code for an ID token by calling the
provider's token address itself, over TLS, with the client secret and the PKCE verifier. An ID token received that way,
straight from the token endpoint, needs no signature check (OpenID Connect Core 3.1.3.7); what is checked is who issued it
(``iss``), who it is for (``aud``), that it has not expired, that its ``nonce`` is the one this sign-in made, and that the
provider calls the email address verified. Nothing the browser carries is trusted: it only brings the code and the state.

Two providers do not speak OpenID Connect and have their own small adapters here, chosen by the provider's ``kind``:

* Facebook (OAuth 2, no ID token): the code is traded for an access token at the Graph API, which is then asked who it belongs to.
  Facebook returns an email address only when the person has a confirmed one, so a returned address counts as verified.
* Steam (OpenID 2.0, no keys and no email address): the browser brings Steam's signed answer, and the server hands it back to
  Steam to ask whether Steam really signed it. Only the answer's checked claimed id (the SteamID64) is used.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx2 as httpx

from .. import wallclock
from ..settings import ExternalProvider

# The provider is asked once for its published settings, then each call waits no longer than this.
TIMEOUT = 10.0
# Facebook Login: the Graph API version is part of every address (Facebook retires old ones after about two years).
FACEBOOK_VERSION = "v23.0"
FACEBOOK_AUTHORIZATION = f"https://www.facebook.com/{FACEBOOK_VERSION}/dialog/oauth"
FACEBOOK_TOKEN = f"https://graph.facebook.com/{FACEBOOK_VERSION}/oauth/access_token"
FACEBOOK_ME = f"https://graph.facebook.com/{FACEBOOK_VERSION}/me"
# Steam's OpenID 2.0 address (it is both where the browser goes and where the answer is checked).
STEAM_LOGIN = "https://steamcommunity.com/openid/login"
STEAM_NAMESPACE = "http://specs.openid.net/auth/2.0"
STEAM_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
STEAM_CLAIMED_ID = re.compile(r"^https://steamcommunity\.com/openid/id/(\d{1,20})$")
# What Steam's answer must sign, or it proves nothing about who signed in or for which sign-in.
STEAM_MUST_SIGN = {"op_endpoint", "claimed_id", "identity", "return_to", "response_nonce"}
# Steam's answer carries about a dozen openid.* values; more than this is not Steam.
STEAM_MAX_PARAMS = 30
STEAM_MAX_VALUE = 1024
# An ID token a little past its expiry is a clock a little off, not a stolen token.
CLOCK_LEEWAY = 60
# Steam's answer must be this fresh, in seconds.
STEAM_NONCE_WINDOW = 600


class SignInError(ValueError):
    """A sign-in that cannot go on. ``reason`` is a short word the account page turns into a message."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(detail or reason)
        self.reason = reason


@dataclass(frozen=True)
class Endpoints:
    issuer: str
    authorization: str
    token: str
    # Where the provider's signing keys are (empty when unknown: only needed to check the provider's own messages).
    jwks: str = ""


@dataclass(frozen=True)
class Flow:
    """What one sign-in remembers between sending the browser away and getting it back (kept in the session cookie)."""

    provider: str
    state: str
    nonce: str
    verifier: str
    # "login" signs in or signs up; "link" adds the provider to the logged-in account $account_id.
    purpose: str
    account_id: int | None = None
    # Where the account page should send the player after a good login (a name the page knows, never an address).
    next: str | None = None
    # With next="oidc": the signed request of the sign-in provider that is waiting for this login (checked again at /oidc/resume).
    request: str | None = None

    def dump(self) -> dict:
        return {key: value for key, value in self.__dict__.items()}


@dataclass(frozen=True)
class Identity:
    """Who the provider says signed in."""

    subject: str
    email: str
    email_verified: bool


def new_flow(provider: ExternalProvider, purpose: str, account_id: int | None = None, next: str | None = None, request: str | None = None) -> Flow:
    return Flow(
        provider=provider.name,
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(48),
        purpose=purpose,
        account_id=account_id,
        next=next,
        request=request,
    )


def challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def redirect_uri(public_url: str, provider: ExternalProvider) -> str:
    return f"{public_url.rstrip('/')}/api/external/{provider.name}/callback"


async def endpoints(http: httpx.AsyncClient, provider: ExternalProvider) -> Endpoints:
    """Where to send the browser and the code: from settings.toml if given, else the provider's published settings."""
    if provider.kind == "facebook":
        return Endpoints("facebook", FACEBOOK_AUTHORIZATION, FACEBOOK_TOKEN)
    if provider.kind == "steam":
        return Endpoints("steam", STEAM_LOGIN, STEAM_LOGIN)
    if provider.authorization_endpoint and provider.token_endpoint:
        return Endpoints(provider.issuer, provider.authorization_endpoint, provider.token_endpoint, provider.jwks_uri or "")
    address = provider.issuer.rstrip("/") + "/.well-known/openid-configuration"
    try:
        answer = await http.get(address, timeout=TIMEOUT)
        answer.raise_for_status()
        found = answer.json()
        return Endpoints(
            str(found["issuer"]),
            str(found["authorization_endpoint"]),
            str(found["token_endpoint"]),
            provider.jwks_uri or str(found.get("jwks_uri") or ""),
        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise SignInError("failed", f"{provider.name}: could not read the provider's settings") from error


def steam_return_to(public_url: str, provider: ExternalProvider, flow: Flow) -> str:
    """Steam brings the browser back here; the state rides in the address (Steam signs the whole address, so it cannot be swapped)."""
    return redirect_uri(public_url, provider) + "?" + urlencode({"state": flow.state})


def authorize_address(provider: ExternalProvider, where: Endpoints, public_url: str, flow: Flow) -> str:
    if provider.kind == "steam":
        realm = public_url.rstrip("/")
        query = {
            "openid.ns": STEAM_NAMESPACE,
            "openid.mode": "checkid_setup",
            "openid.return_to": steam_return_to(public_url, provider, flow),
            "openid.realm": realm,
            "openid.identity": STEAM_IDENTIFIER_SELECT,
            "openid.claimed_id": STEAM_IDENTIFIER_SELECT,
        }
        return where.authorization + "?" + urlencode(query)
    if provider.kind == "facebook":
        # Facebook ignores PKCE today; it is sent so the code is already bound to this browser if Facebook starts checking it.
        query = {
            "response_type": "code",
            "client_id": provider.client_id,
            "redirect_uri": redirect_uri(public_url, provider),
            "scope": "email",
            "state": flow.state,
            "code_challenge": challenge(flow.verifier),
            "code_challenge_method": "S256",
        }
        return where.authorization + "?" + urlencode(query)
    query = {
        "response_type": "code",
        "client_id": provider.client_id,
        "redirect_uri": redirect_uri(public_url, provider),
        "scope": " ".join(provider.scopes),
        "state": flow.state,
        "nonce": flow.nonce,
        "code_challenge": challenge(flow.verifier),
        "code_challenge_method": "S256",
    }
    separator = "&" if "?" in where.authorization else "?"
    return where.authorization + separator + urlencode(query)


def _claims(id_token: str) -> dict:
    """The claims of $id_token (its signature is not read: see the module's note)."""
    try:
        payload = id_token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise SignInError("failed", "the ID token is not readable") from None
    if not isinstance(claims, dict):
        raise SignInError("failed", "the ID token is not readable")
    return claims


def _expected_issuer(issuer: str, claims: dict) -> str:
    # Microsoft's shared address names its tenant with a placeholder; the token says which tenant it came from.
    return issuer.replace("{tenantid}", str(claims.get("tid", ""))) if "{tenantid}" in issuer else issuer


def check_claims(provider: ExternalProvider, where: Endpoints, claims: dict, nonce: str) -> Identity:
    if claims.get("iss") != _expected_issuer(where.issuer, claims):
        raise SignInError("failed", "the ID token is from another issuer")
    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if provider.client_id not in audiences or (len(audiences) > 1 and claims.get("azp") != provider.client_id):
        raise SignInError("failed", "the ID token is for another client")
    expires = claims.get("exp")
    if not isinstance(expires, (int, float)) or isinstance(expires, bool) or expires + CLOCK_LEEWAY < wallclock.timestamp():
        raise SignInError("failed", "the ID token has expired")
    given = claims.get("nonce")
    if not isinstance(given, str) or not secrets.compare_digest(given, nonce):
        raise SignInError("failed", "the ID token is not for this sign-in")
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise SignInError("failed", "the ID token has no subject")
    email = claims.get("email")
    verified = claims.get("email_verified")
    return Identity(
        subject=subject,
        email=email.strip() if isinstance(email, str) else "",
        email_verified=verified is True or verified == "true",
    )


async def complete(
    http: httpx.AsyncClient, provider: ExternalProvider, where: Endpoints, public_url: str, flow: Flow, code: str
) -> Identity:
    """Trades $code for the ID token and checks it. Raises SignInError when the provider refuses or the token does not check out."""
    if provider.kind == "facebook":
        return await _complete_facebook(http, provider, where, public_url, flow, code)
    try:
        answer = await http.post(
            where.token,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri(public_url, provider),
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code_verifier": flow.verifier,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        answer.raise_for_status()
        id_token = answer.json()["id_token"]
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise SignInError("failed", f"{provider.name}: the code was not accepted") from error
    if not isinstance(id_token, str):
        raise SignInError("failed", "the ID token is not readable")
    return check_claims(provider, where, _claims(id_token), flow.nonce)


async def _complete_facebook(
    http: httpx.AsyncClient, provider: ExternalProvider, where: Endpoints, public_url: str, flow: Flow, code: str
) -> Identity:
    """Trades $code for an access token, then asks the Graph API whose it is. The token never leaves this function."""
    try:
        answer = await http.post(
            where.token,
            data={
                "code": code,
                "redirect_uri": redirect_uri(public_url, provider),
                "client_id": provider.client_id,
                "client_secret": provider.client_secret,
                "code_verifier": flow.verifier,
            },
            headers={"Accept": "application/json"},
            timeout=TIMEOUT,
        )
        answer.raise_for_status()
        token = answer.json()["access_token"]
        if not isinstance(token, str) or not token:
            raise ValueError("no access token")
        # The proof shows the call comes from the app server that holds the secret (the app can require it).
        proof = hmac.new(provider.client_secret.encode(), token.encode(), hashlib.sha256).hexdigest()
        who = await http.get(
            FACEBOOK_ME,
            params={"fields": "id,email", "appsecret_proof": proof},
            headers={"Accept": "application/json", "Authorization": f"Bearer {token}"},
            timeout=TIMEOUT,
        )
        who.raise_for_status()
        found = who.json()
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
        raise SignInError("failed", f"{provider.name}: the code was not accepted") from error
    subject = found.get("id") if isinstance(found, dict) else None
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise SignInError("failed", "Facebook gave no id")
    email = found.get("email")
    email = email.strip() if isinstance(email, str) else ""
    # Facebook gives an address only when the person has a confirmed one.
    return Identity(subject=subject, email=email, email_verified=bool(email))


def _steam_nonce_is_fresh(nonce: str) -> bool:
    """Steam's response_nonce starts with the time it was made (2026-10-15T06:33:36Z...); an old answer is a replayed one."""
    stamp = re.match(r"^(\d{4})-(\d\d)-(\d\d)T(\d\d):(\d\d):(\d\d)Z", nonce)
    if stamp is None:
        return False
    try:
        made = datetime(*map(int, stamp.groups()), tzinfo=UTC).timestamp()
    except ValueError:
        return False
    return abs(wallclock.timestamp() - made) <= STEAM_NONCE_WINDOW


async def complete_steam(http: httpx.AsyncClient, provider: ExternalProvider, public_url: str, flow: Flow, params: dict[str, str]) -> Identity:
    """Checks Steam's answer ($params: the ``openid.*`` values of the redirect) and returns who signed in.

    Checked here: it is an answer (not a cancel) from Steam's address, for this sign-in (``return_to`` is the address this flow
    made, state included), it signed the parts that matter, the claimed id is a Steam id, and the nonce is fresh. Then Steam is
    asked whether it made the signature (``check_authentication``), which also spends the nonce so a copy cannot be replayed.
    """
    if len(params) > STEAM_MAX_PARAMS or any(len(value) > STEAM_MAX_VALUE for value in params.values()):
        raise SignInError("failed", "Steam's answer is not readable")
    if params.get("openid.mode") == "cancel":
        raise SignInError("denied", "Steam sign-in was cancelled")
    if params.get("openid.mode") != "id_res" or params.get("openid.ns") != STEAM_NAMESPACE:
        raise SignInError("failed", "Steam's answer is not an answer")
    if params.get("openid.op_endpoint") != STEAM_LOGIN:
        raise SignInError("failed", "the answer is not from Steam")
    if params.get("openid.return_to") != steam_return_to(public_url, provider, flow):
        raise SignInError("failed", "the answer is for another sign-in")
    signed = set((params.get("openid.signed") or "").split(","))
    if not STEAM_MUST_SIGN <= signed:
        raise SignInError("failed", "Steam did not sign what it must")
    claimed = STEAM_CLAIMED_ID.match(params.get("openid.claimed_id", ""))
    if claimed is None or params.get("openid.identity") != params["openid.claimed_id"]:
        raise SignInError("failed", "the answer names no Steam account")
    if not _steam_nonce_is_fresh(params.get("openid.response_nonce", "")):
        raise SignInError("failed", "the answer is too old")
    try:
        answer = await http.post(
            STEAM_LOGIN,
            data={**params, "openid.mode": "check_authentication"},
            headers={"Accept": "text/plain"},
            timeout=TIMEOUT,
        )
        answer.raise_for_status()
        text = answer.text
    except httpx.HTTPError as error:
        raise SignInError("failed", f"{provider.name}: Steam could not be asked") from error
    if "is_valid:true" not in text.splitlines():
        raise SignInError("failed", "Steam did not stand behind the answer")
    # Steam gives no email address: the player is known only by the Steam id.
    return Identity(subject=claimed.group(1), email="", email_verified=False)
