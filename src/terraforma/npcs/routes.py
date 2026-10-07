"""The NPC calls: a hero talks to someone where they stand, and goes on one answer at a time. Every call names the hero. The
reads need a login; the rest also need the CSRF token. Each is its own route with a strict model for what it takes."""

from functools import partial

from fastapi import APIRouter, HTTPException, status
from pydantic import Field

from ..accounts.routes import Strict
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameInn, GameNpcs, GameReach, GameRules, GameTowns
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..towns import service as towns
from . import inn as inns
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}")


class Next(Strict):
    """$choice is the index of the answer picked from the prompt's options, or null for Next, or to cancel."""

    choice: int | None = Field(default=None, ge=0, le=1000)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.NoSuchNpc):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.NpcError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("/npcs")
async def npcs_here(hero_id: Id, account: CurrentAccount, db: Db, reach: GameReach) -> list[dict]:
    """The people the hero could talk to from where they stand."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return [{"id": npc.id, "key": npc.key, "name": npc.name} for npc in await service.npcs_here(db, reach, hero)]


@router.post("/npcs/{npc_id}/talk")
async def talk(hero_id: Id, npc_id: Id, account: ActingAccount, db: Db, npcs: GameNpcs, reach: GameReach, inn: GameInn, rules: GameRules, economy: GameEconomy) -> dict:
    """The hero starts talking to the NPC. Refused (409) unless the hero may talk to them from here and is not in a fight.
    Answers with what was said (``events``: text and cues, in order), what the NPC asks (``prompt``) and whether it ended."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        return await service.talk(db, npcs, reach, hero, npc_id, partial(inns.rest, db, inn, rules, economy), economy)
    except (heroes.HeroError, service.NpcError) as error:
        raise refuse(error) from error


@router.get("/dialog")
async def dialog(hero_id: Id, account: CurrentAccount, db: Db) -> dict:
    """The conversation the hero is in and what it last asked, or ``{"talking": false}``."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return await service.current(db, hero)


@router.post("/dialog/next")
async def next_step(hero_id: Id, body: Next, account: ActingAccount, db: Db, npcs: GameNpcs, reach: GameReach, inn: GameInn, rules: GameRules, economy: GameEconomy, town: GameTowns) -> dict:
    """Goes on: Next (no choice), or the answer picked. A warp into a town suspends the party there."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        frame = await service.answer(db, npcs, reach, hero, body.choice, partial(inns.rest, db, inn, rules, economy), economy)
        await towns.settle_hero(db, town, hero.id)
        return frame
    except (heroes.HeroError, service.NpcError) as error:
        raise refuse(error) from error


@router.post("/dialog/leave")
async def leave(hero_id: Id, account: ActingAccount, db: Db) -> dict:
    """The hero walks away from the conversation."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    await service.leave(db, hero)
    return {"talking": False}
