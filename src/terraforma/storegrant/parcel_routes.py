"""The player's side of the store's parcel: see what waits, and take it into a hero's storage or pack.

Each call is its own route with a strict body. The read needs a login (``CurrentAccount``); the one that changes something needs a login and the
CSRF token (``ActingAccount``) and is rate-limited per account.
"""

from fastapi import APIRouter, Request
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameStorage
from ..heroes import inventory, service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..storage import service as storage_service
from ..storage.routes import refuse as refuse_storage
from . import service

router = APIRouter(prefix="/api")


class Collect(Strict):
    item: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,63}$")
    qty: int = Field(default=1, ge=1, le=service.MAX_PARCEL_QTY)


@router.get("/parcel")
async def parcel(account: CurrentAccount, db: Db) -> dict:
    """What waits in the caller's parcel: item key -> quantity."""
    return {"parcel": await service.parcel(db, account.id)}


@router.post("/heroes/{hero_id}/parcel/collect")
async def collect(hero_id: Id, body: Collect, request: Request, account: ActingAccount, db: Db, storage: GameStorage) -> dict:
    """Takes up to qty of an item from the parcel to the hero's side (the game's storage first, then the packs the storage shares with); what
    does not fit stays in the parcel. Answers how many were taken and how many wait."""
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await service.collect(db, storage, account.id, hero, body.item, body.qty)
    except (heroes.HeroError, inventory.InventoryError, storage_service.StorageError, service.GrantError) as error:
        raise refuse_storage(error) from error
