"""Saying, reading, muting and flagging, through the game's ``Chat``.

Service functions, not calls: the calls (``chat.routes``) and a game's own code both use them. They raise ``ChatError`` (a message the
player can read); the caller's transaction rolls back. Every channel is handled the same way: the kind (``hooks.ChannelKind``) says
who is in it and who hears what, and nothing here knows one kind from another."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..accounts.service import handle_key
from ..models import Account
from .hooks import Channel, ChannelKind, Place, place_of
from .models import ACTIONED, DISMISSED, NOTE_MAX, OPEN, ChatFlag, ChatMessage, ChatMute


class ChatError(ValueError):
    """A chat call that can't be done, in words the player can read."""


class NotFound(ChatError):
    """There is no such channel, message or player."""


@dataclass(frozen=True)
class Line:
    """A message as a reader sees it."""

    id: int
    sender: str
    body: str
    at: datetime
    mine: bool


async def _open(session, game, account: Account, kind: str, key: str) -> tuple[ChannelKind, str]:
    """The channel's kind and stored key, when the player is in it."""
    found = game.chat.kind(kind)
    stored = await found.resolve(session, game, account.id, key) if found else None
    if found is None or stored is None:
        raise NotFound("there is no such channel")
    if not await found.member(session, game, account.id, stored):
        raise ChatError("you are not in that channel")
    return found, stored


async def channels(session: AsyncSession, game, account: Account) -> list[Channel]:
    return [channel for each in game.chat.kinds for channel in await each.channels(session, game, account.id)]


async def speak(session: AsyncSession, game, account: Account, kind: str, key: str, body: str) -> ChatMessage:
    if not account.handle:
        raise ChatError("choose a handle before you chat")
    _, stored = await _open(session, game, account, kind, key)
    text = game.chat.clean(body)
    if not text:
        raise ChatError("say something")
    if len(text) > game.chat.body_max:
        raise ChatError(f"a message is at most {game.chat.body_max} characters")
    refused = await game.chat.may_speak(session, account, kind, stored, text)
    if refused:
        raise ChatError(refused)
    place = await place_of(session, account.id)
    message = ChatMessage(kind=kind, key=stored, sender_id=account.id, body=text, map_id=place.map_id if place else None, x=place.x if place else None, y=place.y if place else None)
    session.add(message)
    await session.flush()
    return message


async def _muted(session, account_id: int) -> set[int]:
    return set((await session.scalars(select(ChatMute.muted_id).where(ChatMute.account_id == account_id))).all())


async def _hears(session, game, found: ChannelKind, reader_id: int, message: ChatMessage) -> bool:
    spoke = Place(message.map_id, message.x, message.y) if message.map_id is not None else None
    return await found.hears(session, game, reader_id, spoke)


async def read(session: AsyncSession, game, account: Account, kind: str, key: str, after: int = 0, limit: int | None = None) -> list[Line]:
    """What the player may see in the channel: with ``after`` 0 the latest ``limit`` lines, otherwise the lines after that number,
    oldest first. Lines from players they muted, and ones said out of their hearing, are left out."""
    found, stored = await _open(session, game, account, kind, key)
    limit = min(limit or game.chat.history, game.chat.history)
    query = select(ChatMessage, Account.handle).join(Account, Account.id == ChatMessage.sender_id).where(ChatMessage.kind == kind, ChatMessage.key == stored)
    muted = await _muted(session, account.id)
    # More rows than asked for are read (and filtered) in batches, so a run of muted lines does not hide the ones behind it.
    lines: list[Line] = []
    cursor = None
    while len(lines) < limit:
        rows = query.where(ChatMessage.id > after) if after else query
        if cursor is not None:
            rows = rows.where(ChatMessage.id < cursor) if not after else rows.where(ChatMessage.id > cursor)
        rows = rows.order_by(ChatMessage.id.asc() if after else ChatMessage.id.desc()).limit(limit * 2)
        batch = (await session.execute(rows)).all()
        if not batch:
            break
        for message, handle in batch:
            if message.sender_id not in muted and await _hears(session, game, found, account.id, message):
                lines.append(Line(message.id, handle or "?", message.body, message.created_at, message.sender_id == account.id))
                if len(lines) == limit:
                    break
        cursor = batch[-1][0].id
        if len(batch) < limit * 2:
            break
    return lines if after else lines[::-1]


async def mute(session: AsyncSession, account: Account, handle: str) -> None:
    other = await session.scalar(select(Account.id).where(Account.handle_key == handle_key(handle)))
    if other is None:
        raise NotFound("there is no player with that handle")
    if other == account.id:
        raise ChatError("you can't mute yourself")
    if other not in await _muted(session, account.id):
        session.add(ChatMute(account_id=account.id, muted_id=other))
        await session.flush()


async def unmute(session: AsyncSession, account: Account, handle: str) -> None:
    other = await session.scalar(select(Account.id).where(Account.handle_key == handle_key(handle)))
    if other is not None:
        await session.execute(delete(ChatMute).where(ChatMute.account_id == account.id, ChatMute.muted_id == other))


async def muted_handles(session: AsyncSession, account: Account) -> list[str]:
    rows = await session.scalars(select(Account.handle).join(ChatMute, ChatMute.muted_id == Account.id).where(ChatMute.account_id == account.id).order_by(Account.handle_key))
    return [each for each in rows.all() if each]


async def flag(session: AsyncSession, game, account: Account, message_id: int, reason: str, note: str = "") -> ChatFlag:
    """The player reports a message they can see. Reporting the same message twice changes nothing."""
    if reason not in game.chat.flag_reasons:
        raise ChatError(f"the reason is one of: {', '.join(game.chat.flag_reasons)}")
    message = await session.get(ChatMessage, message_id)
    if message is None:
        raise NotFound("there is no such message")
    found = game.chat.kind(message.kind)
    # The player must be able to see the message to report it. Membership is asked of the stored key itself (a whisper's key is
    # the pair of players, which no handle resolves to).
    if found is None or not await found.member(session, game, account.id, message.key) or not await _hears(session, game, found, account.id, message):
        raise NotFound("there is no such message")
    if message.sender_id == account.id:
        raise ChatError("you can't flag your own message")
    existing = await session.scalar(select(ChatFlag).where(ChatFlag.message_id == message_id, ChatFlag.reporter_id == account.id))
    if existing is not None:
        return existing
    row = ChatFlag(message_id=message_id, reporter_id=account.id, reason=reason, note=note.strip()[:NOTE_MAX])
    session.add(row)
    await session.flush()
    return row


async def flags(session: AsyncSession, state: str = OPEN, limit: int = 50) -> list[dict]:
    """The flags in a state, oldest first, each with the message and the lines said just before it in its channel."""
    rows = (await session.scalars(select(ChatFlag).where(ChatFlag.state == state).order_by(ChatFlag.id).limit(limit))).all()
    out = []
    for row in rows:
        message = await session.get(ChatMessage, row.message_id)
        before = (await session.execute(
            select(ChatMessage, Account.handle).join(Account, Account.id == ChatMessage.sender_id)
            .where(ChatMessage.kind == message.kind, ChatMessage.key == message.key, ChatMessage.id < message.id)
            .order_by(ChatMessage.id.desc()).limit(5)
        )).all()
        out.append({
            "id": row.id, "state": row.state, "reason": row.reason, "note": row.note, "flagged_at": row.created_at,
            "reporter": await session.scalar(select(Account.handle).where(Account.id == row.reporter_id)),
            "message": {"id": message.id, "channel": message.kind, "sender": await session.scalar(select(Account.handle).where(Account.id == message.sender_id)),
                        "sender_id": message.sender_id, "body": message.body, "at": message.created_at},
            "before": [{"id": each.id, "sender": handle, "body": each.body, "at": each.created_at} for each, handle in reversed(before)],
        })
    return out


async def settle(session: AsyncSession, admin: Account, flag_id: int, state: str) -> ChatFlag:
    """An admin settles a flag as ``actioned`` (the game acted on it) or ``dismissed``. What the action is stays the game's own."""
    if state not in (ACTIONED, DISMISSED):
        raise ChatError("settle a flag as actioned or dismissed")
    row = await session.get(ChatFlag, flag_id)
    if row is None:
        raise NotFound("there is no such flag")
    row.state, row.resolved_by = state, admin.id
    await session.flush()
    return row
