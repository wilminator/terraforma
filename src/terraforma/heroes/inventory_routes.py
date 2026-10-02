"""The inventory calls: look at it, rearrange it, discard from it, wield and put away."""

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db
from . import inventory, service
from .routes import Id, refuse

router = APIRouter(prefix="/api/heroes/{hero_id}")


class Move(Strict):
    from_position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    to_position: int = Field(ge=0, le=inventory.MAX_ITEMS)


class Discard(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    qty: int = Field(default=1, ge=1, le=inventory.MAX_ITEM_QTY)


class Equip(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)
    # Which hand, arm or ammunition slot for the sided slots: 0 left, 1 right.
    side: int = Field(default=0, ge=0, le=1)


class Unequip(Strict):
    position: int = Field(ge=0, le=inventory.MAX_ITEMS)


def outcome(result: inventory.EquipResult):
    """Success is 200 with the slots; anything else says what was in the way (404, 409 or 422)."""
    body = {"outcome": result.outcome.value}
    if result.outcome is inventory.EquipOutcome.SUCCESS:
        return {**body, "slots": list(result.slots)}
    code = {
        inventory.EquipOutcome.NOT_FOUND: status.HTTP_404_NOT_FOUND,
        inventory.EquipOutcome.NOT_EQUIPABLE: status.HTTP_422_UNPROCESSABLE_CONTENT,
    }.get(result.outcome, status.HTTP_409_CONFLICT)
    if result.occupying_position is not None:
        body["occupying_position"] = result.occupying_position
    return JSONResponse(body, status_code=code)


@router.get("/inventory")
async def look(hero_id: Id, account: CurrentAccount, db: Db) -> dict:
    try:
        return await inventory.view(db, await service.own_hero(db, account, hero_id))
    except service.HeroError as error:
        raise refuse(error) from error


@router.post("/inventory/move")
async def move(hero_id: Id, body: Move, account: ActingAccount, db: Db) -> dict:
    try:
        hero = await service.own_hero(db, account, hero_id)
        await inventory.move_item(db, hero, body.from_position, body.to_position)
        return await inventory.view(db, hero)
    except (service.HeroError, inventory.InventoryError) as error:
        raise refuse(error) from error


@router.post("/inventory/discard")
async def discard(hero_id: Id, body: Discard, account: ActingAccount, db: Db) -> dict:
    """Throws away up to qty of the stack (all of it, and its equipment slots, if that's the lot)."""
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    removed = await inventory.remove_item(db, hero, body.position, body.qty)
    if removed == 0:
        raise refuse(service.NotFound("there's nothing in that position"))
    return {"discarded": removed, **await inventory.view(db, hero)}


@router.post("/equip")
async def equip(hero_id: Id, body: Equip, account: ActingAccount, db: Db):
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return outcome(await inventory.equip(db, hero, body.position, body.side))


@router.post("/unequip")
async def unequip(hero_id: Id, body: Unequip, account: ActingAccount, db: Db):
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return outcome(await inventory.unequip(db, hero, body.position))
