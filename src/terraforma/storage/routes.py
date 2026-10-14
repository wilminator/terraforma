"""The storage calls: look at it, put an item in, take one out, use one from there. Each is its own route."""

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules, GameStorage
from ..heroes import inventory, service
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service as storage_service

router = APIRouter(prefix="/api/heroes/{hero_id}/storage")


class Move(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


class StoredPosition(Strict):
    # Storage can hold more stacks than a pack, so the position is only bounded loosely here and checked against what is there.
    position: int = Field(ge=0, le=1000)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


class UseStored(Strict):
    position: int = Field(ge=0, le=1000)
    # Who it is used on: one of the caller's own heroes (the user, when left out).
    target_hero_id: int | None = Field(default=None, ge=1)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, storage_service.StorageError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def look(hero_id: Id, account: CurrentAccount, db: Db, storage: GameStorage) -> dict:
    try:
        return await storage_service.view(db, storage, await service.own_hero(db, account, hero_id))
    except service.HeroError as error:
        raise refuse(error) from error


@router.post("/put")
async def put(hero_id: Id, body: Move, account: ActingAccount, db: Db, storage: GameStorage) -> dict:
    """Moves up to qty of the pack's stack at a position into storage (it must all fit, and not be worn)."""
    try:
        hero = await service.own_hero(db, account, hero_id)
        await storage_service.put(db, storage, hero, body.position, body.qty)
        return await storage_service.view(db, storage, hero)
    except (service.HeroError, inventory.InventoryError, storage_service.StorageError) as error:
        raise refuse(error) from error


@router.post("/take")
async def take(hero_id: Id, body: StoredPosition, account: ActingAccount, db: Db, storage: GameStorage) -> dict:
    """Moves up to qty of storage's stack at a position into the hero's pack (it must all fit)."""
    try:
        hero = await service.own_hero(db, account, hero_id)
        await storage_service.take(db, storage, hero, body.position, body.qty)
        return await storage_service.view(db, storage, hero)
    except (service.HeroError, inventory.InventoryError, storage_service.StorageError) as error:
        raise refuse(error) from error


@router.post("/use")
async def use(hero_id: Id, body: UseStored, account: ActingAccount, db: Db, storage: GameStorage, rules: GameRules) -> dict:
    """Spends one of the item at a position of storage on the hero or another of the caller's, outside a fight, if the game lets it."""
    try:
        hero = await service.own_hero(db, account, hero_id)
        target = hero if body.target_hero_id is None else await service.own_hero(db, account, body.target_hero_id)
        return await storage_service.use(db, storage, rules, hero, body.position, target)
    except (service.HeroError, inventory.InventoryError, storage_service.StorageError) as error:
        raise refuse(error) from error
