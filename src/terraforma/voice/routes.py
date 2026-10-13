"""The voice calls: whether voice is on and for which channels, and a link to join a channel's room. Each is its own route."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db
from ..chat import service as chat
from ..chat.routes import Key, Kind, game_of, refuse
from . import service
from .discord import VoiceError, provider_for

router = APIRouter(prefix="/api/voice")


class Join(Strict):
    kind: Kind
    key: Key


@router.get("")
async def voice(request: Request, account: CurrentAccount) -> dict:
    """Whether voice is on, and the kinds of chat channel that have a room (for a page to show a Voice button)."""
    on = provider_for(request.app.state.settings) is not None
    return {"enabled": on, "kinds": sorted(game_of(request).voice.kinds) if on else []}


@router.post("/join")
async def join(body: Join, request: Request, account: ActingAccount, db: Db) -> dict:
    """A link that takes the player into the voice room of a channel they are in: single-use, good for a few minutes."""
    await limited(request, ratelimit.VOICE_BY_ACCOUNT, str(account.id))
    try:
        invite = await service.join(db, game_of(request), provider_for(request.app.state.settings), account, body.kind, body.key)
    except service.VoiceOff as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except chat.ChatError as error:
        raise refuse(error) from error
    except VoiceError as error:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(error)) from error
    return {"url": invite.url, "seconds": invite.seconds}
