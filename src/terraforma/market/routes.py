"""The shop calls. A hero shops from their conversation: the NPC they are talking to must be waiting on a ``vend``, ``hawk`` or
``shop`` (``/dialog/next`` leaves the shop and goes on with the text). The reads need a login; buying and selling also need the
CSRF token and are limited per account like trades."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameMarket, GameNpcs, GameRules
from ..heroes import inventory
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..npcs import service as npcs
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}/dialog/shop")


class Buy(Strict):
    item: str = Field(min_length=1, max_length=64)
    qty: int = Field(default=1, ge=1, le=service.MAX_QTY)


class Sell(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, (service.ShopError, npcs.NpcError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def shop(hero_id: Id, account: CurrentAccount, db: Db, hooks: GameNpcs, market: GameMarket, rules: GameRules, economy: GameEconomy) -> dict:
    """The wares with prices, what the hero could sell and for how much, and their gold."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        prompt = await npcs.activity(db, hooks, hero, service.COMMANDS)
        return await service.view(db, market, rules, economy, hero, prompt)
    except (heroes.HeroError, npcs.NpcError, service.ShopError) as error:
        raise refuse(error) from error


@router.post("/buy")
async def buy(hero_id: Id, body: Buy, request: Request, account: ActingAccount, db: Db, hooks: GameNpcs, market: GameMarket, rules: GameRules, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        prompt = await npcs.activity(db, hooks, hero, service.COMMANDS)
        return await service.buy(db, market, rules, economy, hero, prompt, body.item, body.qty)
    except (heroes.HeroError, npcs.NpcError, service.ShopError) as error:
        raise refuse(error) from error


@router.post("/sell")
async def sell(hero_id: Id, body: Sell, request: Request, account: ActingAccount, db: Db, hooks: GameNpcs, market: GameMarket, rules: GameRules, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        prompt = await npcs.activity(db, hooks, hero, service.COMMANDS)
        return await service.sell(db, market, rules, economy, hero, prompt, body.position, body.qty)
    except (heroes.HeroError, npcs.NpcError, service.ShopError) as error:
        raise refuse(error) from error
