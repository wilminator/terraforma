"""The hero and team calls. Each is its own route; the ones that change something need login and the CSRF token."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, GameRules
from . import service

router = APIRouter(prefix="/api")

Id = Annotated[int, Path(ge=1)]


class NewHero(Strict):
    name: str = Field(min_length=1, max_length=24)
    job: str = Field(min_length=1, max_length=64)


class NameOnly(Strict):
    name: str = Field(min_length=1, max_length=24)


class HeroRef(Strict):
    hero_id: int = Field(ge=1)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.NameTaken):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


@router.get("/jobs")
async def jobs(account: CurrentAccount, db: Db) -> list[dict]:
    """The jobs a new hero can take, with the stats a hero of each starts with."""
    return await service.list_jobs(db)


@router.get("/heroes")
async def heroes(account: CurrentAccount, db: Db) -> list[dict]:
    return await service.list_heroes(db, account)


@router.post("/heroes", status_code=status.HTTP_201_CREATED)
async def create_hero(body: NewHero, account: ActingAccount, db: Db) -> dict:
    """A new level 1 hero with the job's starting stats, standing on the hub map."""
    try:
        hero = await service.create_hero(db, account, body.name, body.job)
    except service.HeroError as error:
        raise refuse(error) from error
    return next(entry for entry in await service.list_heroes(db, account) if entry["id"] == hero.id)


@router.post("/heroes/{hero_id}/rename")
async def rename_hero(hero_id: Id, body: NameOnly, account: ActingAccount, db: Db) -> dict:
    try:
        hero = await service.rename_hero(db, account, hero_id, body.name)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"id": hero.id, "name": hero.name}


@router.post("/heroes/{hero_id}/delete")
async def delete_hero(hero_id: Id, account: ActingAccount, db: Db) -> dict:
    try:
        await service.delete_hero(db, account, hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}


@router.get("/teams")
async def teams(account: CurrentAccount, db: Db) -> list[dict]:
    return await service.list_teams(db, account)


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(body: NameOnly, account: ActingAccount, db: Db) -> dict:
    try:
        team = await service.create_team(db, account, body.name)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"id": team.id, "name": team.name, "members": []}


@router.post("/teams/{team_id}/rename")
async def rename_team(team_id: Id, body: NameOnly, account: ActingAccount, db: Db) -> dict:
    try:
        team = await service.rename_team(db, account, team_id, body.name)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"id": team.id, "name": team.name}


@router.post("/teams/{team_id}/delete")
async def delete_team(team_id: Id, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        await service.delete_team(db, account, team_id, alliances)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}


@router.post("/teams/{team_id}/add-hero")
async def add_hero_to_team(team_id: Id, body: HeroRef, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    try:
        member = await service.add_to_team(db, account, team_id, body.hero_id, rules.party_size)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"team_id": team_id, "hero_id": member.hero_id, "slot": member.slot}


@router.post("/teams/{team_id}/remove-hero")
async def remove_hero_from_team(team_id: Id, body: HeroRef, account: ActingAccount, db: Db) -> dict:
    try:
        await service.remove_from_team(db, account, team_id, body.hero_id)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}
