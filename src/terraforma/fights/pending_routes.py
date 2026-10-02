"""The pending-drop calls: see the drops waiting for a hero, say need, want or pass, and hand one out. Each is its own route."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from ..heroes import service
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import pending

router = APIRouter(prefix="/api/heroes/{hero_id}/pending-drops")


class Choose(Strict):
    choice: Literal["need", "want", "pass"]


class Assign(Strict):
    to_hero_id: int = Field(ge=1)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, pending.PendingError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def waiting(hero_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    try:
        hero = await service.own_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return [await pending.view(db, each, hero) for each in await pending.for_hero(db, hero)]


@router.post("/{drop_id}/choose")
async def choose(hero_id: Id, drop_id: Id, body: Choose, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.DROP_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await pending.view(db, await pending.choose(db, hero, drop_id, body.choice), hero)
    except (service.HeroError, pending.PendingError) as error:
        raise refuse(error) from error


@router.post("/{drop_id}/assign")
async def assign(hero_id: Id, drop_id: Id, body: Assign, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    await limited(request, ratelimit.DROP_BY_ACCOUNT, str(account.id))
    try:
        hero = await service.own_hero(db, account, hero_id)
        return await pending.view(db, await pending.assign(db, rules, hero, drop_id, body.to_hero_id), hero)
    except (service.HeroError, pending.PendingError) as error:
        raise refuse(error) from error
