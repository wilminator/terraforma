"""The hero and team calls. Each is its own route; the ones that change something need login and the CSRF token.

A team is made with its heroes, and a hero never exists without a team: the calls that make heroes are the team's. What the
player may do with a saved team's heroes (remove, replace, move, exchange) is the game's to allow (``Game.roster``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, GameRoster, GameRules
from . import service

router = APIRouter(prefix="/api")

Id = Annotated[int, Path(ge=1)]


class NewHero(Strict):
    name: str = Field(min_length=1, max_length=24)
    job: str = Field(min_length=1, max_length=64)


class NameOnly(Strict):
    name: str = Field(min_length=1, max_length=24)


class NewTeam(Strict):
    name: str = Field(min_length=1, max_length=24)
    heroes: list[NewHero] = Field(default=[], max_length=service.TEAM_LIMIT)


class ToTeam(Strict):
    team_id: int = Field(ge=1)


class Swap(Strict):
    hero_id: int = Field(ge=1)
    with_hero_id: int = Field(ge=1)


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


@router.get("/team-rules")
async def team_rules(account: CurrentAccount, rules: GameRules) -> dict:
    """What the game says about teams: ``team_min`` and ``team_max`` heroes in one (never above 5) and ``max_teams`` for a player."""
    return service.team_rules(rules)


@router.get("/heroes")
async def heroes(account: CurrentAccount, db: Db) -> list[dict]:
    return await service.list_heroes(db, account)


@router.post("/heroes/{hero_id}/rename")
async def rename_hero(hero_id: Id, body: NameOnly, account: ActingAccount, db: Db) -> dict:
    try:
        hero = await service.rename_hero(db, account, hero_id, body.name)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"id": hero.id, "name": hero.name}


@router.post("/heroes/{hero_id}/move")
async def move_hero(hero_id: Id, body: ToTeam, account: ActingAccount, db: Db, rules: GameRules, roster: GameRoster) -> dict:
    """Moves the hero to another of the caller's teams, if the game's roster rule allows it (refused by default)."""
    try:
        member = await service.move_hero(db, account, hero_id, body.team_id, rules, roster)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"team_id": member.team_id, "hero_id": member.hero_id, "slot": member.slot}


@router.post("/heroes/swap")
async def swap_heroes(body: Swap, account: ActingAccount, db: Db, roster: GameRoster) -> dict:
    """Exchanges two heroes of two of the caller's teams, if the game's roster rule allows it (refused by default)."""
    try:
        await service.swap_heroes(db, account, body.hero_id, body.with_hero_id, roster)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}


@router.get("/teams")
async def teams(account: CurrentAccount, db: Db) -> list[dict]:
    return await service.list_teams(db, account)


@router.post("/teams", status_code=status.HTTP_201_CREATED)
async def create_team(body: NewTeam, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Makes a team, with its heroes if the browser sends them (at most the game's ``team_max``). A team with fewer than ``team_min``
    heroes is incomplete and cannot play until the player has filled it."""
    try:
        team = await service.save_team(db, account, body.name, [(each.name, each.job) for each in body.heroes], rules)
    except service.HeroError as error:
        raise refuse(error) from error
    return next(entry for entry in await service.list_teams(db, account) if entry["id"] == team.id)


@router.post("/teams/{team_id}/rename")
async def rename_team(team_id: Id, body: NameOnly, account: ActingAccount, db: Db) -> dict:
    try:
        team = await service.rename_team(db, account, team_id, body.name)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"id": team.id, "name": team.name}


@router.post("/teams/{team_id}/delete")
async def delete_team(team_id: Id, account: ActingAccount, db: Db, alliances: GameAlliances, roster: GameRoster) -> dict:
    """Deletes the team and its heroes, if the game's roster rule allows it (it does, by default)."""
    try:
        await service.disband_team(db, account, team_id, roster, alliances)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}


@router.post("/teams/{team_id}/heroes", status_code=status.HTTP_201_CREATED)
async def add_hero(team_id: Id, body: NewHero, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """A new level 1 hero in the team's first free place, while the team has room (``team_max``)."""
    try:
        hero = await service.add_new_hero(db, account, team_id, body.name, body.job, rules)
    except service.HeroError as error:
        raise refuse(error) from error
    return next(entry for entry in await service.list_heroes(db, account) if entry["id"] == hero.id)


@router.post("/teams/{team_id}/heroes/{hero_id}/replace", status_code=status.HTTP_201_CREATED)
async def replace_hero(team_id: Id, hero_id: Id, body: NewHero, account: ActingAccount, db: Db, roster: GameRoster) -> dict:
    """Removes the hero and makes a new one in their place, if the game's roster rule allows it (refused by default)."""
    try:
        hero = await service.replace_hero(db, account, team_id, hero_id, body.name, body.job, roster)
    except service.HeroError as error:
        raise refuse(error) from error
    return next(entry for entry in await service.list_heroes(db, account) if entry["id"] == hero.id)


@router.post("/teams/{team_id}/heroes/{hero_id}/delete")
async def remove_hero(team_id: Id, hero_id: Id, account: ActingAccount, db: Db, rules: GameRules, roster: GameRoster) -> dict:
    """Removes the hero for good, if the team keeps its minimum and the game's roster rule allows it (refused by default)."""
    try:
        await service.remove_hero(db, account, team_id, hero_id, rules, roster)
    except service.HeroError as error:
        raise refuse(error) from error
    return {"ok": True}
