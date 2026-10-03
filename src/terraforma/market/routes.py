"""The shop calls. A hero shops where they stand: every call names the hero and the shop, and the shop must be on the
hero's tile. The reads need a login; buying and selling also need the CSRF token and are limited per account like trades."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameMarket, GameRules
from ..heroes import inventory
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}/shops")


class Buy(Strict):
    item: str = Field(min_length=1, max_length=64)
    qty: int = Field(default=1, ge=1, le=service.MAX_QTY)


class Sell(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.NoSuchShop):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.ShopError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def shops_here(hero_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    """The shops on the tile the hero stands on."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return [{"id": shop.id, "key": shop.key, "name": shop.name} for shop in await service.shops_here(db, hero)]


@router.get("/{shop_id}")
async def shop(hero_id: Id, shop_id: Id, account: CurrentAccount, db: Db, market: GameMarket, rules: GameRules, economy: GameEconomy) -> dict:
    """The shop's stock with prices, what the hero could sell and for how much, and their gold."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await service.view(db, market, rules, economy, hero, shop_id)
    except (heroes.HeroError, service.ShopError) as error:
        raise refuse(error) from error


@router.post("/{shop_id}/buy")
async def buy(hero_id: Id, shop_id: Id, body: Buy, request: Request, account: ActingAccount, db: Db, market: GameMarket, rules: GameRules, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await service.buy(db, market, rules, economy, hero, shop_id, body.item, body.qty)
    except (heroes.HeroError, service.ShopError) as error:
        raise refuse(error) from error


@router.post("/{shop_id}/sell")
async def sell(hero_id: Id, shop_id: Id, body: Sell, request: Request, account: ActingAccount, db: Db, market: GameMarket, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await service.sell(db, market, economy, hero, shop_id, body.position, body.qty)
    except (heroes.HeroError, service.ShopError) as error:
        raise refuse(error) from error
