"""The trading calls: give gold or an item to another hero, and read the hero's ledger. Each is its own route."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy
from ..heroes import inventory, service
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service as trading

router = APIRouter(prefix="/api/heroes/{hero_id}")


class GiveGold(Strict):
    to_hero_id: int = Field(ge=1)
    amount: int = Field(ge=1, le=trading.MAX_GOLD)


class GiveItem(Strict):
    to_hero_id: int = Field(ge=1)
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, trading.Refused):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, trading.TradeError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.post("/give-gold")
async def give_gold(hero_id: Id, body: GiveGold, request: Request, account: ActingAccount, db: Db, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return {"gold": await trading.give_gold(db, economy, hero, body.to_hero_id, body.amount)}
    except (service.HeroError, trading.TradeError) as error:
        raise refuse(error) from error


@router.post("/give-item")
async def give_item(hero_id: Id, body: GiveItem, request: Request, account: ActingAccount, db: Db, economy: GameEconomy) -> dict:
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        await trading.give_item(db, economy, hero, body.to_hero_id, body.position, body.qty)
        return await inventory.view(db, hero, economy)
    except (service.HeroError, trading.TradeError) as error:
        raise refuse(error) from error


@router.get("/trades")
async def trades(hero_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    try:
        return await trading.history(db, await service.own_hero(db, account, hero_id))
    except service.HeroError as error:
        raise refuse(error) from error
