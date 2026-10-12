"""The monster queue calls: see where you stand, volunteer, leave, and say you are still there. Each is its own route.
A volunteer needs a login like any player; the calls that change something also need the CSRF token."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from ..fights import live
from . import service

router = APIRouter(prefix="/api/monsters/queue")
#: The calls on the monsters of one running fight.
fight_router = APIRouter(prefix="/api/fights/{fight_id}/monsters")
FightId = Annotated[int, Path(ge=1)]


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


@fight_router.post("/back")
async def back(fight_id: FightId, request: Request, account: ActingAccount, db: Db) -> dict:
    """The player who commands a fight's monsters says they are back after the AI took over: they command again from the next round."""
    await limited(request, ratelimit.MONSTER_QUEUE_BY_ACCOUNT, str(account.id))
    try:
        seat = await service.come_back(db, await live.get_record(db, fight_id), account.id)
    except live.FightError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except service.MonsterError as error:
        raise refuse(error) from error
    return {"seat": service.view_of(seat)}


@fight_router.post("/take-over")
async def take_over(fight_id: FightId, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """An admin sits in a running fight as the controller of its monsters (403 for anyone else). The round's end is pushed back so they can
    finish the playback and decide, and the fight's watchers are told the new deadline."""
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may sit in a running fight")
    try:
        record = await live.get_record(db, fight_id)
        seat = await service.take_over(db, rules, record, account.id)
        deadline = live.aware(record.round_deadline).isoformat() if record.round_deadline else None
    except live.FightError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except service.MonsterError as error:
        raise refuse(error) from error
    await request.app.state.fights.push(fight_id, {"type": "deadline", "fight": fight_id, "deadline": deadline})
    return {"seat": service.view_of(seat), "deadline": deadline}
