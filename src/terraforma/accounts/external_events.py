"""What an external sign-in provider tells this site on its own: "this person deleted their account there".

Some providers (the shared account service is one) keep the person's account and tell every site the person used when it is
deleted, so the site can drop what it holds for them. The message is a signed token (a Security Event Token, RFC 8417: a JWT
with the header ``typ`` ``secevent+jwt``) posted as ``{"token": "..."}`` to ``/api/external/<name>/events``. The call has no
player login and no CSRF token: it is not made by a browser, and the signature is the proof. It is off unless the provider's
section in ``settings.toml`` says ``deletion_events = true``.

The provider's deletion page also asks, with a signed request (a JWT with the header ``typ`` ``account-lookup+jwt`` and the person as its ``sub`` claim) at ``/api/external/<name>/account-lookup``,
whether the person has an account here: the answer is ``{"exists": bool}`` and, when true, a short ``summary``.

The token is checked against the provider's published signing keys (found by the ``kid`` in its header; the algorithm must be
one of ``ALGORITHMS``, never ``none`` or a shared-secret one), and its issuer, audience (this site's ``client_id``) and expiry.
The token must carry a ``jti`` (the provider's one-per-message id); it is not remembered, because a deletion done twice is the same as once and nothing else is acted on. Only the event ``<issuer>/events/account-deleted`` is understood; a token with any other event is accepted and ignored.

What a deletion does here: the link between the person's account here and the provider is removed, and every login of that
account ends. The account itself (heroes, teams, history) is not deleted: that is a decision for the game, and an account with a
password of its own still works. A provider that is the only way into an account leaves it with no way in.
"""

import logging

import jwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field
from sqlalchemy import func, select

from ..api.deps import Db, client_address
from ..heroes.models import Team
from ..models import Account
from . import oidc, ratelimit, service
from .common import Strict, limited
from .external_routes import provider_of

router = APIRouter(prefix="/api/external")
log = logging.getLogger(__name__)

# Both are asymmetric: a message the site could also have made itself (a shared secret) proves nothing.
ALGORITHMS = ("ES256", "RS256")
EVENT_TYPE = "secevent+jwt"
# A lookup request is not an event, and the header says so, so neither can pass as the other.
LOOKUP_TYPE = "account-lookup+jwt"
DELETED = "/events/account-deleted"


class EventRequest(Strict):
    token: str = Field(min_length=1, max_length=4096)


async def verify(http, provider, where: oidc.Endpoints, token: str, typ: str = EVENT_TYPE) -> dict:
    """The claims of $token if the provider signed it for this site, else SignInError (``reason`` is always "failed")."""
    if not where.jwks:
        raise oidc.SignInError("failed", f"{provider.name}: no signing keys are known")
    try:
        header = jwt.get_unverified_header(token)
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS or str(header.get("typ", "")).lower() != typ or not isinstance(header.get("kid"), str):
            raise oidc.SignInError("failed", "the message header is not accepted")
        answer = await http.get(where.jwks, timeout=oidc.TIMEOUT)
        answer.raise_for_status()
        key = next((jwk for jwk in answer.json()["keys"] if jwk.get("kid") == header["kid"]), None)
        if key is None:
            raise oidc.SignInError("failed", "the message is signed with an unknown key")
        return jwt.decode(
            token,
            jwt.PyJWK.from_dict(key, algorithm=algorithm).key,
            algorithms=[algorithm],
            audience=provider.client_id,
            issuer=where.issuer,
            leeway=oidc.CLOCK_LEEWAY,
            options={"require": ["exp", "iat", "iss", "aud", "jti"]},
        )
    except oidc.SignInError:
        raise
    except Exception as error:  # a bad token, a bad key set, an unreachable provider: all the same answer
        raise oidc.SignInError("failed", f"{provider.name}: the message was not accepted") from error


async def checked(name: str, token: str, request: Request, typ: str = EVENT_TYPE) -> tuple[dict, oidc.Endpoints]:
    """The claims of the signed message in the request body, if $name's provider signed it for this site (else 404 or 400)."""
    provider = provider_of(request, name)
    if not provider.deletion_events:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such sign-in provider")
    await limited(request, ratelimit.EXTERNAL_EVENTS_BY_ADDRESS, client_address(request))
    http = request.app.state.http_client
    try:
        where = await oidc.endpoints(http, provider)
        return await verify(http, provider, where, token, typ), where
    except oidc.SignInError as error:
        log.warning("refused a message from %s: %s", name, error)
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the message is not accepted") from error


@router.post("/{name}/events")
async def events(name: str, body: EventRequest, request: Request, db: Db) -> dict:
    """The provider's "account deleted" message: unlinks the person and ends their logins here. Answers the same whether or not they were known."""
    claims, where = await checked(name, body.token, request)
    events = claims.get("events")
    deleted = events.get(where.issuer.rstrip("/") + DELETED) if isinstance(events, dict) else None
    if deleted is None:
        return {"ok": True}
    subject = deleted.get("sub") if isinstance(deleted, dict) else None
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the message is not accepted")
    identity = await service.find_identity(db, name, subject)
    if identity is not None:
        account = await db.get(Account, identity.account_id)
        await db.delete(identity)
        if account is not None:
            account.session_version += 1
    return {"ok": True}


@router.post("/{name}/account-lookup")
async def account_lookup(name: str, body: EventRequest, request: Request, db: Db) -> dict:
    """Does the person ($sub of the signed message) have an account here? The provider shows the answer on its deletion page."""
    claims, _ = await checked(name, body.token, request, LOOKUP_TYPE)
    subject = claims.get("sub")
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the message is not accepted")
    identity = await service.find_identity(db, name, subject)
    if identity is None:
        return {"exists": False}
    teams = await db.scalar(select(func.count()).select_from(Team).where(Team.account_id == identity.account_id))
    return {"exists": True, "summary": f"an account with {teams} team{'' if teams == 1 else 's'}"}
