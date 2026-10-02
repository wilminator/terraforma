"""The live fight calls: command a fighter, look at a fight, watch it by its public name, and the fight socket.

Each call is its own route with a strict model for its arguments. A call that changes something needs a login and
the CSRF token (``ActingAccount``); one that only reads needs a login (``CurrentAccount``). The fight socket checks
the Origin header and the login too. Whoever acts commits their own heroes' fighters, nobody else's.

These calls open their own transactions, so what they push to the fight's watchers has been committed.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from ..api.deps import ActingAccount, CurrentAccount
from ..api.security import SESSION_ACCOUNT, SESSION_VERSION, same_origin
from ..economy import Economy, TeamGold
from ..models import Account
from . import live, store
from .combatant import Command
from .models import FightRecord
from .rules import Rules


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class FighterRef(Strict):
    """A fighter's place in a fight: party, group, and place in the group."""

    party: int = Field(ge=0, le=7)
    group: int = Field(ge=0, le=3)
    character: int = Field(ge=0, le=9)

    def address(self) -> tuple[int, int, int]:
        return (self.party, self.group, self.character)


class FightCommand(Strict):
    fighter: FighterRef
    command: Literal["attack_left", "attack_right", "item", "equip", "skill", "spell", "defend", "flee", "equip_weapon"]
    # Which item, skill or spell, by its place in the fighter's list.
    using: int = Field(default=0, ge=0, le=255)
    target: FighterRef


COMMANDS = {
    "attack_left": Command.ATTACK_LEFT, "attack_right": Command.ATTACK_RIGHT, "item": Command.ITEM, "equip": Command.EQUIP,
    "skill": Command.SKILL, "spell": Command.SPELL, "defend": Command.DEFEND, "flee": Command.RUN, "equip_weapon": Command.EQUIP_AMMO,
}

FightId = Annotated[int, Path(ge=1)]
Guid = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]

router = APIRouter(prefix="/api/fights")


def rules_of(carrier) -> Rules:
    """The game's fight rules, from a request or the app itself."""
    game = getattr(carrier, "app", carrier).state.game
    return game.rules if game else Rules()


def economy_of(carrier) -> Economy:
    game = getattr(carrier, "app", carrier).state.game
    return game.economy if game else TeamGold()


def refuse(error: live.FightError) -> HTTPException:
    code = {live.NotFound: status.HTTP_404_NOT_FOUND, live.NotYours: status.HTTP_403_FORBIDDEN}.get(type(error), status.HTTP_409_CONFLICT)
    return HTTPException(code, str(error))


@router.post("/{fight_id}/commands", status_code=status.HTTP_202_ACCEPTED)
async def fight_command(fight_id: FightId, body: FightCommand, request: Request, account: ActingAccount) -> dict:
    """Commits a command for one of the caller's fighters. The round plays at once if that was the last one needed."""
    rules, channels = rules_of(request), request.app.state.fights
    try:
        async with request.app.state.sessionmaker() as session, session.begin():
            record = await live.get_record(session, fight_id)
            number = await live.submit_command(session, record, account.id, body.fighter.address(), COMMANDS[body.command], body.using, body.target.address(), rules)
            result = await live.resolve_round(session, record, rules, economy_of(request)) if await live.everyone_committed(session, record, rules) else None
    except live.FightError as error:
        raise refuse(error) from error
    except store.SequenceConflict:  # the timer played the round a moment before: the command missed it
        return {"accepted": True, "round": None, "played": True}
    # Said only once it is stored. A command is not told to the watchers (the other side must not see it coming):
    # only that the fighter has committed, and the round when it plays.
    await channels.push(fight_id, {"type": "committed", "fight": fight_id, "round": number, "fighter": body.fighter.model_dump()})
    if result is not None:
        await channels.push(fight_id, result.message(fight_id))
    return {"accepted": True, "round": number, "played": result is not None}


@router.get("/watch/{guid}")
async def watch(guid: Guid, request: Request, account: CurrentAccount) -> dict:
    """A fight by its public name, for anyone logged in to watch."""
    async with request.app.state.sessionmaker() as session:
        record = await session.scalar(select(FightRecord).where(FightRecord.guid == guid))
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such fight")
        return await live.view(session, record, rules_of(request))


@router.get("/{fight_id}")
async def fight_view(fight_id: FightId, request: Request, account: CurrentAccount) -> dict:
    """A fight the caller has a hero in."""
    async with request.app.state.sessionmaker() as session:
        try:
            record = await live.get_record(session, fight_id)
        except live.FightError as error:
            raise refuse(error) from error
        if account.id not in await live.watchers(session, record):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "that isn't your fight")
        return await live.view(session, record, rules_of(request), account.id)


async def logged_in(socket: WebSocket) -> int | None:
    """The account id behind the socket's session, if it is a live login."""
    account_id = socket.session.get(SESSION_ACCOUNT)
    if account_id is None:
        return None
    async with socket.app.state.sessionmaker() as session:
        account = await session.get(Account, account_id)
    if account is None or account.session_version != socket.session.get(SESSION_VERSION):
        return None
    return account.id


async def fight_socket(socket: WebSocket, fight_id: FightId, guid: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None) -> None:
    """Pushes a fight's events to the page as the rounds play. Only the fight's players may listen (by its number), or
    anyone logged in who knows its public name (``?guid=``). The page only listens: commands go through the call."""
    account_id = await logged_in(socket) if same_origin(socket) else None
    if account_id is None:
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    async with socket.app.state.sessionmaker() as session:
        record = await session.get(FightRecord, fight_id)
        allowed = record is not None and (record.guid == guid if guid else account_id in await live.watchers(session, record))
    if not allowed:
        await socket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    channels = socket.app.state.fights
    await socket.accept()
    channels.join(fight_id, socket)
    try:
        await socket.send_json({"type": "joined", "fight": fight_id, "watching": channels.watching(fight_id)})
        while True:
            await socket.receive_text()  # anything it sends is ignored
    except WebSocketDisconnect:
        pass
    finally:
        channels.leave(fight_id, socket)
