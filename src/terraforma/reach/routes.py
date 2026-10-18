"""The nearby call: what a hero can reach for an action from where they stand. Needs a login (it only reads), and the hero
must be the caller's."""

from typing import Annotated

from fastapi import APIRouter, Path, Request
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameReach, GameRelations, GameRules
from ..heroes import inventory, service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import helping, service

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


class HelpWithItem(Strict):
    """$position is the item's place in the caller's hero's pack; the target is hero number $member (from 0) of party $party_id,
    in the order the party lists its heroes."""

    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    party_id: int = Field(ge=1)
    member: int = Field(ge=0, le=64)


@router.post("/help/use-item")
async def help_with_item(
    hero_id: Id, body: HelpWithItem, request: Request, account: ActingAccount, db: Db, reach: GameReach, rules: GameRules, relations: GameRelations
) -> dict:
    """Spends one of the hero's items on a hero of a party in reach (healing, mana, a revive) unless the hero's team counts that
    party as an enemy. The target's resources before and after come back; the other player is not asked."""
    await limited(request, ratelimit.HELP_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await helping.help_with_item(db, reach, rules, relations, hero, body.position, body.party_id, body.member)
    except (heroes.HeroError, inventory.InventoryError) as error:
        raise refuse_hero(error) from error
