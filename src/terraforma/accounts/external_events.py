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
The token must carry a ``jti`` (the provider's one-per-message id); it is not remembered, because acting on a message twice is the same as once. Only the events ``<issuer>/events/account-deleted``, ``account-unlinked``, ``account-suspended`` and ``account-reinstated`` are understood; a token with any other event is accepted and ignored.

What a deletion does here: nothing is deleted yet. The account service wants a 200 only once everything the site holds for the person is gone, and
this site has no routine that erases it, so for a person it knows the answer is 500 (their logins end, the link to the provider stays so a
replay can find them). An erase that answers 200 comes with its own slice.
"""

import logging

import jwt
from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import func, select

from ..api.deps import Db, client_address
from ..heroes.models import Team
from ..models import Account
from . import blocks, oidc, ratelimit, service
from .common import Strict, limited
from .external_routes import provider_of

router = APIRouter(prefix="/api/external")
log = logging.getLogger(__name__)

# Only ES256: the account service signs with nothing else, and a message the site could also have made itself (a shared secret) proves nothing.
ALGORITHMS = ("ES256",)
EVENT_TYPE = "secevent+jwt"
# A lookup request is not an event, and the header says so, so neither can pass as the other.
LOOKUP_TYPE = "account-lookup+jwt"
DELETED = "/events/account-deleted"
UNLINKED = "/events/account-unlinked"
SUSPENDED = "/events/account-suspended"
REINSTATED = "/events/account-reinstated"


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


def subject_of(events: object, key: str) -> str | None:
    """The person named by the event $key in a message's ``events`` (None: the message holds no such event); a 400 when the event names nobody valid."""
    if not isinstance(events, dict) or key not in events:
        return None
    payload = events[key]
    subject = payload.get("sub") if isinstance(payload, dict) else None
    if not isinstance(subject, str) or not 0 < len(subject) <= 255:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "the message is not accepted")
    return subject


@router.post("/{name}/events")
async def events(name: str, body: EventRequest, request: Request, db: Db) -> Response:
    """The provider's messages about a person: suspended or reinstated (acted on), deleted or unlinked (not erased yet), anything else (ignored).

    ``account-suspended``: the person's logins end and no login works by any method (password, provider, remembered browser) until
    ``account-reinstated`` from the same provider lifts it; nothing is deleted. Both answer 200, also for a person nobody here knows.

    ``account-deleted`` and ``account-unlinked`` mean "delete everything you hold for this person", and the answer 200 says that is done and
    committed, after which the provider forgets the message. The engine has no erase yet, so for a person it knows the answer is 500 (the
    provider retries for about three days, then alerts its operator, who can replay the message once the erase exists) and nothing is
    deleted: the link stays, because the link is how a replay finds the person. Their logins do end.
    """
    claims, where = await checked(name, body.token, request)
    events = claims.get("events")
    issuer = where.issuer.rstrip("/")
    suspended = subject_of(events, issuer + SUSPENDED)
    block = events.get(issuer + SUSPENDED) if suspended is not None and isinstance(events, dict) else {}
    reinstated = subject_of(events, issuer + REINSTATED)
    erasing = subject_of(events, issuer + DELETED) or subject_of(events, issuer + UNLINKED)
    refuse = False
    for subject, kind in ((suspended, "suspend"), (reinstated, "reinstate"), (erasing, "erase")):
        if subject is None:
            continue
        identity = await service.find_identity(db, name, subject)
        account = await db.get(Account, identity.account_id) if identity is not None else None
        if account is None:
            continue
        if kind == "suspend":
            await blocks.block(db, account, name, str(block.get("kind", "")), block.get("until"))
        elif kind == "reinstate":
            if account.suspended_by == name:  # only the provider that blocked it lifts it
                await blocks.lift(db, account)
        else:
            account.session_version += 1
            refuse = True
    if refuse:
        return JSONResponse({"ok": False, "reason": "erasing a person's data is not built yet"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
    return JSONResponse({"ok": True})


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
