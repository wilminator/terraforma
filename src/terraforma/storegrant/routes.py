"""The server-to-server call for the game's store: grant (or take) Challenge Tokens and items, and tell the player.

Like the Challenge Token purchase call it has no login, cookie or CSRF token: the store's backend proves itself with the shared
secret in ``settings.toml`` (``store_grant_secret``), sent as ``Authorization: Bearer <secret>``. With no secret set, the call
does not exist (404). The store never learns a player's email; the engine mails the player the transaction message.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, game_name, send
from ..api.deps import Db, GameRules
from ..challenge.routes import server_call_allowed
from ..challenge.service import ChallengeError
from ..mail import Mail
from ..models import Account
from . import service

router = APIRouter(prefix="/api/server/store")
log = logging.getLogger(__name__)

MAX_TOKENS = 1_000_000_000


class Grant(Strict):
    account_id: int = Field(ge=1)
    # Challenge Tokens to add; negative to take them back (a spend).
    tokens: int = Field(default=0, ge=-MAX_TOKENS, le=MAX_TOKENS)
    # Content key -> quantity (at least 1).
    items: dict[str, Annotated[int, Field(ge=1, le=service.MAX_PARCEL_QTY)]] = Field(default_factory=dict, max_length=service.MAX_ITEMS_PER_GRANT)
    # What the player is told about the transaction (plain text).
    message: str = Field(default="", max_length=1000)
    # Mail the message to the player (the default). Without a confirmed email address nothing is sent either way.
    email: bool = True
    # The store's idempotency key: send it again (a retry) and the grant is made once. 58 characters at most.
    key: str = Field(min_length=8, max_length=58, pattern=r"^[A-Za-z0-9_.:-]+$")


@router.post("/grant")
async def grant(
    request: Request,
    body: Grant,
    db: Db,
    rules: GameRules,
    authorization: Annotated[str | None, Header()] = None,
) -> dict:
    """Grants tokens and items to an account. Repeating a call with the same key and the same grant changes nothing and answers
    the same, with ``duplicate`` true (and mails nothing again). 404: no such account or item (or the call is off). 409: the key
    was used for a different grant, the balance would go below zero, the purse cap would be passed, or the parcel is full
    (nothing changes). 422: the game's rule refused, with its reason in ``detail``."""
    await server_call_allowed(request, authorization, request.app.state.settings.store_grant_secret, ratelimit.STORE_GRANT_BY_ADDRESS)
    try:
        done = await service.grant(db, rules, body.account_id, body.tokens, body.items, body.message, body.key)
    except (service.UnknownAccount, service.UnknownItem) as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except (service.KeyReused, service.ParcelFull, ChallengeError) as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except service.GrantError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    emailed = False
    if body.email and not done.duplicate:
        emailed = await _tell(request, await db.get(Account, body.account_id), body)
    return {"balance": done.balance, "duplicate": done.duplicate, "emailed": emailed}


async def _tell(request: Request, account: Account, body: Grant) -> bool:
    """Mails the transaction message to the player. A failure to mail never undoes the grant: it answers ``emailed`` false."""
    if not account.email or account.email_confirmed_at is None:
        return False
    lines = [body.message] if body.message else []
    if body.tokens:
        lines.append(f"Challenge Tokens: {body.tokens:+d}")
    lines += [f"{key} x {qty}" for key, qty in sorted(body.items.items())]
    try:
        await send(request, Mail(
            to=account.email,
            subject=f"{game_name(request)}: a transaction on your account",
            body=f"{account.username},\n\n" + "\n".join(lines) + "\n",
        ))
    except Exception:  # the mail server is the caller's problem, not the grant's
        log.exception("could not mail a store transaction to account %s", account.id)
        return False
    return True
