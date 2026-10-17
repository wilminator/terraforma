"""The marketplace calls: post a listing, search, buy, take one back, and see and collect the team's parcels. Each is its own route."""

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field
from sqlalchemy import select

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameFees, GameMarketplace, GameStorage
from ..delivery import service as delivery
from ..heroes import inventory, service
from ..heroes.models import TeamMember
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service as market

router = APIRouter()


class Stack(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


class Ask(Strict):
    item: str = Field(min_length=1, max_length=64)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


class Post(Strict):
    guild: str = Field(min_length=1, max_length=64)
    items: list[Stack] = Field(min_length=1, max_length=inventory.MAX_ITEMS)
    price_gold: int = Field(default=0, ge=0, le=market.MAX_PRICE)
    price_tokens: int = Field(default=0, ge=0, le=market.MAX_PRICE)
    asks: list[Ask] = Field(default_factory=list, max_length=market.MAX_ASKS)


class Buy(Strict):
    to_guild: str = Field(min_length=1, max_length=64)
    pay_items: list[Stack] = Field(default_factory=list, max_length=inventory.MAX_ITEMS)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, market.MarketplaceError | delivery.DeliveryError):
        gone = "no such" in str(error) or "gone" in str(error) or "not for you" in str(error)
        return HTTPException(status.HTTP_404_NOT_FOUND if gone else status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


def _parcel(shipment, contents: dict) -> dict:
    return {
        "id": shipment.id, "guild": shipment.guild, "purpose": shipment.purpose, "state": shipment.state,
        "arrives_at": shipment.arrives_at, "gold": contents["gold"], "items": contents["items"],
    }


@router.get("/api/market/listings")
async def listings(
    account: CurrentAccount, db: Db, guild: str | None = Query(default=None, max_length=64), text: str | None = Query(default=None, max_length=64),
    max_gold: int | None = Query(default=None, ge=0, le=market.MAX_PRICE), limit: int = Query(default=20, ge=1, le=market.MAX_PAGE),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> list[dict]:
    return await market.search(db, guild, text, max_gold, limit, offset)


@router.post("/api/heroes/{hero_id}/listings")
async def post_listing(
    hero_id: Id, body: Post, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, fees: GameFees, marketplace: GameMarketplace,
) -> dict:
    await limited(request, ratelimit.MARKET_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        listing = await market.post(
            db, economy, fees, marketplace, hero, body.guild, [(s.position, s.qty) for s in body.items], body.price_gold, body.price_tokens,
            [(a.item, a.qty) for a in body.asks],
        )
        return {
            "id": listing.id, "guild": listing.guild, "price_gold": listing.price_gold, "price_tokens": listing.price_tokens,
            "asks": await market.asks_of(db, listing), "goods": await market.goods(db, listing), "expires_at": listing.expires_at,
        }
    except (service.HeroError, market.MarketplaceError) as error:
        raise refuse(error) from error


@router.post("/api/heroes/{hero_id}/listings/{listing_id}/buy")
async def buy_listing(
    hero_id: Id, listing_id: Id, body: Buy, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, fees: GameFees,
    marketplace: GameMarketplace,
) -> dict:
    await limited(request, ratelimit.MARKET_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        parcel = await market.buy(db, economy, fees, marketplace, hero, listing_id, body.to_guild, [(s.position, s.qty) for s in body.pay_items])
        return _parcel(parcel, await delivery.contents(db, parcel))
    except (service.HeroError, market.MarketplaceError) as error:
        raise refuse(error) from error


@router.post("/api/heroes/{hero_id}/listings/{listing_id}/cancel")
async def cancel_listing(
    hero_id: Id, listing_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, storage: GameStorage,
) -> dict:
    await limited(request, ratelimit.MARKET_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        listing = await market.cancel(db, economy, storage, hero, listing_id)
        return {"id": listing.id, "state": listing.state}
    except (service.HeroError, market.MarketplaceError) as error:
        raise refuse(error) from error


@router.get("/api/heroes/{hero_id}/parcels")
async def my_parcels(hero_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    team_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        return []
    return [_parcel(p, await delivery.contents(db, p)) for p in await delivery.parcels(db, to_team_id=team_id)]


@router.post("/api/heroes/{hero_id}/parcels/{parcel_id}/collect")
async def collect_parcel(
    hero_id: Id, parcel_id: Id, request: Request, account: ActingAccount, db: Db, economy: GameEconomy, storage: GameStorage,
) -> dict:
    await limited(request, ratelimit.MARKET_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        parcel = await delivery.collect(db, economy, storage, hero, parcel_id)
        return _parcel(parcel, await delivery.contents(db, parcel))
    except (service.HeroError, delivery.DeliveryError) as error:
        raise refuse(error) from error
