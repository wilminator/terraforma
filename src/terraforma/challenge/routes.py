"""The server-to-server call for crediting Challenge Tokens a player bought.

It is for a game's own backend (a shop or payment service), not for a browser: there is no login, no cookie and no CSRF
token. The caller proves itself with the shared secret in ``settings.toml`` (``challenge_purchase_secret``), sent as
``Authorization: Bearer <secret>``. With no secret set, the call does not exist (404), so a game that does not sell tokens
exposes nothing. The secret is compared in constant time, every attempt is counted against the caller's address, and
logs scrub the header.
"""

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import Db, GameRules, client_address
from . import service

router = APIRouter(prefix="/api/server/challenge")

MAX_PURCHASE = 1_000_000_000


class Purchase(Strict):
    account_id: int = Field(ge=1)
    amount: int = Field(ge=1, le=MAX_PURCHASE)
    # The buyer's idempotency key: send the same key again (a retry) and the tokens are credited once.
    key: str = Field(min_length=8, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")


async def authorised(request: Request, authorization: str | None) -> None:
    secret = request.app.state.settings.challenge_purchase_secret
    if not secret:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await limited(request, ratelimit.CHALLENGE_PURCHASE_BY_ADDRESS, client_address(request))
    scheme, _, given = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(given.encode(), secret.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not allowed", headers={"WWW-Authenticate": "Bearer"})


@router.post("/purchase")
async def credit_purchase(
    request: Request,
    body: Purchase,
    db: Db,
    rules: GameRules,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """Credits tokens an account bought. Repeating a call with the same key, account and amount credits nothing more and
    answers the same, with ``duplicate`` true. 404: no such account (or the call is off). 409: the key was used for a
    different purchase, or the purse cap would be passed (nothing is credited)."""
    await authorised(request, authorization)
    try:
        done = await service.purchase(db, rules, body.account_id, body.amount, body.key)
    except service.UnknownAccount as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (service.PurseFull, service.KeyReused) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {"credited": done.amount, "balance": done.balance, "duplicate": done.duplicate}
