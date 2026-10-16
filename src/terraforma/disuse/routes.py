"""The force-disuse calls: when the player of a hero that holds something the game has retired signs in, the hero goes through a scene.
Every call names the hero. The read needs a login; the others also need the CSRF token. Each is its own route with a strict model."""

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db, GameDisuse, GameEconomy, GameNpcs, GameRules
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}/disuse")


class Next(Strict):
    """$choice is the index of the answer picked from the prompt's options, or null for Next, or to cancel."""

    choice: int | None = Field(default=None, ge=0, le=1000)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.DisuseError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def read(hero_id: Id, account: CurrentAccount, db: Db) -> dict:
    """What the hero has to settle, what would come back to it, and the scene it is in, if any."""
    try:
        return await service.status(db, await heroes.own_hero(db, account, hero_id))
    except heroes.HeroError as error:
        raise refuse(error) from error


@router.post("/start")
async def start(hero_id: Id, account: ActingAccount, db: Db, disuse: GameDisuse, npcs: GameNpcs, rules: GameRules, economy: GameEconomy) -> dict:
    """Starts the hero's scene: what is kept for it comes back first, then the game's scene runs for what it still has to stop using.
    Refused (409) for a hero in a fight. A hero with nothing to settle gets ``ended`` at once."""
    try:
        return await service.start(db, disuse, npcs, await heroes.own_hero(db, account, hero_id), economy, rules)
    except (heroes.HeroError, service.DisuseError) as error:
        raise refuse(error) from error


@router.post("/next")
async def next_step(hero_id: Id, body: Next, account: ActingAccount, db: Db, disuse: GameDisuse, npcs: GameNpcs, rules: GameRules, economy: GameEconomy) -> dict:
    """Goes on in the scene: Next (no choice), or the answer picked."""
    try:
        return await service.answer(db, disuse, npcs, await heroes.own_hero(db, account, hero_id), body.choice, economy, rules)
    except (heroes.HeroError, service.DisuseError) as error:
        raise refuse(error) from error
