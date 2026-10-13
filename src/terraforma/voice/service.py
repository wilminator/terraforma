"""Joining a voice room: the same membership as the chat channel it belongs to, then a room made on the first ask and a fresh link each time."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..chat import service as chat
from ..models import Account
from .hooks import Invite, VoiceProvider
from .models import VoiceRoom


class VoiceOff(chat.ChatError):
    """Voice is not set up for this game."""


async def join(session: AsyncSession, game, provider: VoiceProvider | None, account: Account, kind: str, key: str) -> Invite:
    if provider is None:
        raise VoiceOff("voice is not turned on in this game")
    if kind not in game.voice.kinds:
        raise chat.ChatError("that channel has no voice room")
    # The same questions chat asks: the channel must exist and the player must be in it. (A whisper's handle becomes its pair here.)
    _, stored = await chat._open(session, game, account, kind, key)
    room = await session.scalar(select(VoiceRoom).where(VoiceRoom.kind == kind, VoiceRoom.key == stored))
    if room is None:
        provider_id = await provider.open_room(game.voice.room_name(kind, stored))
        try:
            async with session.begin_nested():
                room = VoiceRoom(kind=kind, key=stored, provider_id=provider_id)
                session.add(room)
                await session.flush()
        except IntegrityError:  # another server made the room first: use theirs, and drop the one just made
            await provider.close_room(provider_id)
            room = await session.scalar(select(VoiceRoom).where(VoiceRoom.kind == kind, VoiceRoom.key == stored))
    return await provider.invite(room.provider_id)
