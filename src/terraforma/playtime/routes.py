"""Play time: look at it, and say that a round has finished showing.

Each call is its own route with a strict model. Saying a round has played out changes something, so it needs a login and
the CSRF token (``ActingAccount``); looking needs a login (``CurrentAccount``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field

from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from ..fights import live
from ..fights.models import FightActionRecord
from . import service

router = APIRouter(prefix="/api")


class PlaybackDone(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    # The round the page has finished showing.
    round: int = Field(ge=1, le=1_000_000)


@router.get("/play-time")
async def play_time(account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """How long the caller has played, in seconds."""
    return {"seconds": await service.seconds_played(db, rules, account.id)}


@router.post("/fights/{fight_id}/playback-done")
async def playback_done(fight_id: Annotated[int, Path(ge=1)], body: PlaybackDone, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """The caller's page has finished showing a round of a fight they have a hero in: the time since the round played counts as play time (up to the Rules' cap)."""
    try:
        record = await live.get_record(db, fight_id)
    except live.FightError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    if account.id not in await live.watchers(db, record):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "that isn't your fight")
    row = await db.get(FightActionRecord, (fight_id, body.round))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "that round hasn't been played")
    if row.played_at is not None:
        await service.credit_playback(db, rules, account.id, live.aware(row.played_at))
    return {"seconds": await service.seconds_played(db, rules, account.id)}
