"""What a hero is under: its own, its team's and its party's standing statuses."""

from fastapi import APIRouter

from ..api.deps import CurrentAccount, Db
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}")


@router.get("/statuses")
async def statuses(hero_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    """The standing statuses the hero is under now (``on``: hero, team or party; the seconds left, none for no end; whether a
    buff-cancelling effect cannot take it off)."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse_hero(error) from error
    return await service.of_hero(db, hero)
