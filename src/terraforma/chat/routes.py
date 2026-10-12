"""The chat calls: the channels a player is in, reading and saying, muting, flagging a message, and the admin's flags. Each is its own route.

Calls that change something need a login and the CSRF token (``ActingAccount``); those that only read need a login (``CurrentAccount``).
Messages are fetched, not pushed: a page asks for the lines after the last one it holds, so any server can answer."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db
from ..game import Game
from . import service
from .models import ACTIONED, DISMISSED, NOTE_MAX, OPEN

router = APIRouter(prefix="/api/chat")

Kind = Annotated[str, Field(pattern=r"^[a-z][a-z_]{0,15}$")]
Key = Annotated[str, Field(min_length=1, max_length=40)]


class Say(Strict):
    kind: Kind
    key: Key
    body: str = Field(min_length=1, max_length=2000)


class Handle(Strict):
    handle: str = Field(min_length=1, max_length=64)


class Flag(Strict):
    message_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=16)
    note: str = Field(default="", max_length=NOTE_MAX)


class Settle(Strict):
    flag_id: int = Field(ge=1)
    state: str = Field(pattern=f"^({ACTIONED}|{DISMISSED})$")


class Ban(Strict):
    handle: str = Field(min_length=1, max_length=64)
    hours: int | None = Field(default=None, ge=1, le=24 * 365)
    reason: str = Field(default="", max_length=NOTE_MAX)


def game_of(request: Request) -> Game:
    return request.app.state.game or Game(name="TerraForma")


def refuse(error: service.ChatError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if isinstance(error, service.NotFound) else status.HTTP_409_CONFLICT, str(error))


def line(each: service.Line) -> dict:
    return {"id": each.id, "sender": each.sender, "body": each.body, "at": each.at, "mine": each.mine}


@router.get("/channels")
async def channels(request: Request, account: CurrentAccount, db: Db) -> dict:
    """The channels the player is in now: each a kind, a key (the channel's address in the other calls) and a name to show; and
    whether the player is an admin (so a page asks for the flags only when it will be answered)."""
    return {"admin": account.is_admin, "channels": [{"kind": each.kind, "key": each.key, "label": each.label} for each in await service.channels(db, game_of(request), account)]}


@router.get("/messages")
async def messages(
    request: Request, account: CurrentAccount, db: Db, kind: Annotated[str, Query(pattern=r"^[a-z][a-z_]{0,15}$")], key: Annotated[str, Query(min_length=1, max_length=40)],
    after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict:
    """The latest lines of a channel, or (with ``after``) the lines after that one, oldest first. A whisper's key is the other player's handle."""
    try:
        return {"messages": [line(each) for each in await service.read(db, game_of(request), account, kind, key, after, limit)]}
    except service.ChatError as error:
        raise refuse(error) from error


@router.post("/say", status_code=status.HTTP_201_CREATED)
async def say(body: Say, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.CHAT_BY_ACCOUNT, str(account.id))
    try:
        message = await service.speak(db, game_of(request), account, body.kind, body.key, body.body)
    except service.ChatError as error:
        raise refuse(error) from error
    return {"id": message.id, "body": message.body}


@router.get("/mutes")
async def mutes(account: CurrentAccount, db: Db) -> dict:
    return {"muted": await service.muted_handles(db, account)}


@router.post("/mute")
async def mute(body: Handle, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.CHAT_REPORT_BY_ACCOUNT, str(account.id))
    try:
        await service.mute(db, account, body.handle)
    except service.ChatError as error:
        raise refuse(error) from error
    return {"muted": await service.muted_handles(db, account)}


@router.post("/unmute")
async def unmute(body: Handle, account: ActingAccount, db: Db) -> dict:
    await service.unmute(db, account, body.handle)
    return {"muted": await service.muted_handles(db, account)}


@router.post("/flag", status_code=status.HTTP_201_CREATED)
async def flag(body: Flag, request: Request, account: ActingAccount, db: Db) -> dict:
    """Reports a message the player can see (for the game's terms of use, or another reason the game allows) for an admin to settle."""
    await limited(request, ratelimit.CHAT_REPORT_BY_ACCOUNT, str(account.id))
    try:
        row = await service.flag(db, game_of(request), account, body.message_id, body.reason, body.note)
    except service.ChatError as error:
        raise refuse(error) from error
    return {"id": row.id, "state": row.state}


@router.get("/reasons")
async def reasons(request: Request, account: CurrentAccount) -> dict:
    """The reasons the game allows for a flag."""
    return {"reasons": list(game_of(request).chat.flag_reasons)}


def admin_only(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "admins only")


@router.get("/admin/flags")
async def admin_flags(account: CurrentAccount, db: Db, state: Annotated[str, Query(pattern=f"^({OPEN}|{ACTIONED}|{DISMISSED})$")] = OPEN) -> dict:
    """The flags in a state (open by default), each with the message and the lines before it."""
    admin_only(account)
    return {"flags": await service.flags(db, state)}


@router.post("/admin/settle")
async def admin_settle(body: Settle, request: Request, account: ActingAccount, db: Db) -> dict:
    admin_only(account)
    try:
        row = await service.settle(db, game_of(request), account, body.flag_id, body.state)
    except service.ChatError as error:
        raise refuse(error) from error
    return {"id": row.id, "state": row.state}


@router.get("/admin/bans")
async def admin_bans(account: CurrentAccount, db: Db) -> dict:
    """The players barred from speaking now, with until when (none: for good)."""
    admin_only(account)
    return {"bans": await service.bans(db)}


@router.post("/admin/ban")
async def admin_ban(body: Ban, account: ActingAccount, db: Db) -> dict:
    """Bars a player from speaking, for ``hours`` or for good when left out. They can still read."""
    admin_only(account)
    try:
        await service.ban(db, account, body.handle, body.hours, body.reason)
    except service.ChatError as error:
        raise refuse(error) from error
    return {"bans": await service.bans(db)}


@router.post("/admin/unban")
async def admin_unban(body: Handle, account: ActingAccount, db: Db) -> dict:
    admin_only(account)
    await service.unban(db, body.handle)
    return {"bans": await service.bans(db)}
