"""The monster queue calls: see where you stand, volunteer, leave, and say you are still there. Each is its own route.
A volunteer needs a login like any player; the calls that change something also need the CSRF token."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules, client_address
from ..api.security import same_origin
from ..fights import live
from ..fights.routes import FightCommand, play_command
from . import service
from .models import MonsterSeat

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


# --- guests: people who play monsters without logging in -----------------------------------------------------------------
#
# No cookie and no login: a guest holds a random token and sends it as ``Authorization: Bearer <token>``. Since nothing rides along
# by itself, another site cannot act for a guest (that is what the CSRF token guards for accounts); instead the calls that change
# something must carry this site's Origin header, and every call counts against the caller's address. The engine's rules refuse
# guests (``Rules.may_queue_monsters``), so these calls only work in a game that accepts them.

guest_router = APIRouter(prefix="/api/monsters/guest")


def _origin_here(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "wrong origin")


async def _guest(request: Request, rules: GameRules, authorization: Annotated[str | None, Header()] = None) -> int:
    """The guest's id, from the bearer token. (Looked up in a transaction of its own, closed before the call goes on, so keeping
    the guest alive never holds a lock while the call plays a round.)"""
    await limited(request, ratelimit.MONSTER_GUEST_BY_ADDRESS, client_address(request))
    scheme, _, token = (authorization or "").partition(" ")
    guest = None
    if scheme.lower() == "bearer" and token:
        async with request.app.state.sessionmaker() as session, session.begin():
            guest = await service.guest_for(session, rules, token)
            guest_id = guest.id if guest is not None else None
    if guest is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not a guest we know", headers={"WWW-Authenticate": "Bearer"})
    return guest_id


async def _acting_guest(request: Request, guest_id: Annotated[int, Depends(_guest)]) -> int:
    _origin_here(request)
    return guest_id


Guest = Annotated[int, Depends(_guest)]
ActingGuest = Annotated[int, Depends(_acting_guest)]


@guest_router.post("/join")
async def guest_join(request: Request, db: Db, rules: GameRules) -> dict:
    """A person who is not logged in volunteers: makes a guest, queues it, and answers with its token (shown once; the page keeps it
    for the guest calls). 409 with the rules' reason when the game does not take guests (the engine's rules do not), and nothing is made."""
    _origin_here(request)
    await limited(request, ratelimit.MONSTER_GUEST_JOIN_BY_ADDRESS, client_address(request))
    guest, token = await service.new_guest(db, rules)
    try:
        await service.join_guest(db, rules, guest.id)
    except service.MonsterError as error:
        raise refuse(error) from error
    return {"token": token, **await service.status_guest(db, rules, guest.id)}


@guest_router.get("")
async def guest_where(guest: Guest, db: Db, rules: GameRules) -> dict:
    """Where the guest stands: queued and at what place, and the fight whose monsters it commands."""
    return await service.status_guest(db, rules, guest)


@guest_router.post("/leave")
async def guest_leave(guest: ActingGuest, db: Db, rules: GameRules) -> dict:
    await service.leave_guest(db, guest)
    return await service.status_guest(db, rules, guest)


@guest_router.post("/check-in")
async def guest_check_in(guest: ActingGuest, db: Db, rules: GameRules) -> dict:
    try:
        await service.check_in_guest(db, rules, guest)
    except service.MonsterError as error:
        raise refuse(error) from error
    return await service.status_guest(db, rules, guest)


async def _own_fight(db, fight_id: int, guest_id: int):
    try:
        record = await live.get_record(db, fight_id)
    except live.FightError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    seat = await db.get(MonsterSeat, fight_id)
    if seat is None or seat.guest_id != guest_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that isn't your fight")
    return record


@guest_router.get("/fights/{fight_id}")
async def guest_fight(fight_id: FightId, guest: Guest, db: Db, rules: GameRules) -> dict:
    """The fight the guest commands the monsters of, as the heroes' page shows it (a guest has no socket: it asks again)."""
    record = await _own_fight(db, fight_id, guest)
    return await live.view(db, record, rules, -guest)


@guest_router.post("/fights/{fight_id}/commands", status_code=status.HTTP_202_ACCEPTED)
async def guest_command(fight_id: FightId, body: FightCommand, request: Request, guest: ActingGuest) -> dict:
    """Commits a command for one of the guest's monsters. The round plays at once if that was the last one needed."""
    async with request.app.state.sessionmaker() as session:
        await _own_fight(session, fight_id, guest)
    return await play_command(request, fight_id, -guest, body)


@guest_router.post("/fights/{fight_id}/back")
async def guest_back(fight_id: FightId, guest: ActingGuest, db: Db) -> dict:
    """The guest says they are back after the AI took over the monsters."""
    record = await _own_fight(db, fight_id, guest)
    try:
        seat = await service.come_back(db, record, -guest)
    except service.MonsterError as error:
        raise refuse(error) from error
    return {"seat": service.view_of(seat)}
