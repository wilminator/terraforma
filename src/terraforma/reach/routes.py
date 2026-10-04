"""The nearby call: what a hero can reach for an action from where they stand. Needs a login (it only reads), and the hero
must be the caller's."""

from typing import Annotated

from fastapi import APIRouter, Path

from ..api.deps import CurrentAccount, Db, GameReach, GameRules
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}")

#: The name of an action: the engine's (talk, invite, open, search, fight, help) and any a game adds.
Action = Annotated[str, Path(pattern=r"^[a-z][a-z_]{0,31}$")]


@router.get("/nearby/{action}")
async def nearby(hero_id: Id, action: Action, account: CurrentAccount, db: Db, reach: GameReach, rules: GameRules) -> dict:
    """What the hero can reach for the action: ``nearby`` lists the NPCs, then the parties (when the game's rule for the
    action shows parties), each nearest first and at most ``Rules.nearby_limit`` in all; ``valid_for`` is how many seconds the
    list may be shown before asking again. The calls that act check the range again when they run."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse_hero(error) from error
    return await service.nearby(db, reach, rules, hero, action)
