"""A player's round time setting: look at it and its costs, and choose.

Each call is its own route with a strict model. Choosing needs a login and the CSRF token (``ActingAccount``);
looking needs a login (``CurrentAccount``). A choice counts for the fights started after it.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict

from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from . import timing

router = APIRouter(prefix="/api/settings")


class RoundTime(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    multiplier: float


def _view(rules, chosen: float) -> dict:
    return {
        "multiplier": chosen,
        "round_seconds": rules.round_length(chosen),
        "options": [{"multiplier": each, "monster_hp_bonus": bonus} for each, bonus in rules.time_multipliers],
    }


@router.get("/round-time")
async def round_time(account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """How much longer the caller's rounds wait, what the game offers and what each costs."""
    return _view(rules, await timing.multiplier_for(db, account.id))


@router.post("/round-time")
async def choose_round_time(body: RoundTime, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Chooses the caller's round time, from what the game offers. It counts for fights started afterwards."""
    try:
        await timing.choose(db, rules, account.id, body.multiplier)
    except timing.NotOffered as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    return _view(rules, body.multiplier)
