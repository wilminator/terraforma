"""The monster queue calls: see where you stand, volunteer, leave, and say you are still there. Each is its own route.
A volunteer needs a login like any player; the calls that change something also need the CSRF token."""

from fastapi import APIRouter, HTTPException, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from . import service

router = APIRouter(prefix="/api/monsters/queue")


def refuse(error: service.MonsterError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(error))


@router.get("")
async def where(account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """Whether the account waits in the queue and where, the fight whose monsters it commands, a lockout, and why it may not volunteer."""
    return await service.status(db, rules, account.id)


@router.post("/join")
async def join(request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Volunteers to command the monsters of the next fight that can take a player (the rules may refuse: 409 with the reason)."""
    await limited(request, ratelimit.MONSTER_QUEUE_BY_ACCOUNT, str(account.id))
    try:
        await service.join(db, rules, account.id)
    except service.MonsterError as error:
        raise refuse(error) from error
    return await service.status(db, rules, account.id)


@router.post("/leave")
async def leave(request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Leaves the queue (nothing happens if the account was not in it)."""
    await limited(request, ratelimit.MONSTER_QUEUE_BY_ACCOUNT, str(account.id))
    await service.leave(db, account.id)
    return await service.status(db, rules, account.id)


@router.post("/check-in")
async def check_in(request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Says the volunteer is still there, so the queue keeps their place (409 if they are not in it)."""
    await limited(request, ratelimit.MONSTER_QUEUE_BY_ACCOUNT, str(account.id))
    try:
        await service.check_in(db, rules, account.id)
    except service.MonsterError as error:
        raise refuse(error) from error
    return await service.status(db, rules, account.id)
