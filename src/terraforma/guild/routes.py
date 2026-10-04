"""The guild calls: a hero uses the guild from their conversation, which must be waiting on ``add_team``, ``find_party`` or
``party_requests``. Adding a team and asking a party finish the activity and go on with the dialog (so the tag's price, which
the dialog charged before it, buys one use); answering requests goes on until the player leaves with ``/dialog/next``. The reads
need a login; the writes also need the CSRF token and are limited per account like trades."""

from functools import partial

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameGuild, GameInn, GameNpcs, GameReach, GameRules
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..npcs import inn as inns
from ..npcs import service as npcs
from . import service

router = APIRouter(prefix="/api/heroes/{hero_id}/dialog/guild")


class AddTeam(Strict):
    team_id: int = Field(ge=1)


class Ask(Strict):
    party_id: int = Field(ge=1)


class Answer(Strict):
    request_id: int = Field(ge=1)
    accept: bool


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, (service.GuildError, npcs.NpcError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def guild(hero_id: Id, account: CurrentAccount, db: Db, hooks: GameNpcs, reach: GameReach) -> dict:
    """What the hero can do at the guild now, by what the conversation is waiting on: their own teams that could be added
    (``add_team``), the open parties and the teams that could ask (``find_party``), or the requests to their party (``party_requests``,
    for its leader)."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        prompt = await npcs.activity(db, hooks, reach, hero, ("add_team", "find_party", "party_requests"))
        command = prompt["command"]
        if command == "add_team":
            return {"command": command, "teams": await service.teams_to_add(db, hero)}
        if command == "find_party":
            return {"command": command, "parties": await service.open_parties(db, hero)}
        return {"command": command, "requests": await service.requests(db, hero)}
    except (heroes.HeroError, npcs.NpcError, service.GuildError) as error:
        raise refuse(error) from error


async def _then_on(db, hooks, reach, inn, rules, economy, hero, result: dict) -> dict:
    return {"result": result, "dialog": await npcs.answer(db, hooks, reach, hero, None, partial(inns.rest, db, inn, rules, economy))}


@router.post("/add-team")
async def add_team(hero_id: Id, body: AddTeam, request: Request, account: ActingAccount, db: Db, hooks: GameNpcs, reach: GameReach, guild: GameGuild, inn: GameInn, rules: GameRules, economy: GameEconomy) -> dict:
    """Adds one of the player's own teams to the hero's party, and goes on with the dialog."""
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        await npcs.activity(db, hooks, reach, hero, ("add_team",))
        result = await service.add_team(db, hero, body.team_id, rules.party_size, guild)
        return await _then_on(db, hooks, reach, inn, rules, economy, hero, result)
    except (heroes.HeroError, npcs.NpcError, service.GuildError) as error:
        raise refuse(error) from error


@router.post("/ask")
async def ask(hero_id: Id, body: Ask, request: Request, account: ActingAccount, db: Db, hooks: GameNpcs, reach: GameReach, guild: GameGuild, inn: GameInn, rules: GameRules, economy: GameEconomy) -> dict:
    """The hero's team asks to join an open party, and the dialog goes on."""
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        await npcs.activity(db, hooks, reach, hero, ("find_party",))
        result = await service.ask(db, guild, hero, body.party_id, rules.party_size)
        return await _then_on(db, hooks, reach, inn, rules, economy, hero, result)
    except (heroes.HeroError, npcs.NpcError, service.GuildError) as error:
        raise refuse(error) from error


@router.post("/answer")
async def answer(hero_id: Id, body: Answer, request: Request, account: ActingAccount, db: Db, hooks: GameNpcs, reach: GameReach, rules: GameRules) -> dict:
    """The party's leader accepts or declines a request. The conversation stays where it is."""
    await limited(request, ratelimit.TRADE_BY_ACCOUNT, str(account.id))
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        await npcs.activity(db, hooks, reach, hero, ("party_requests",))
        return await service.answer(db, hero, body.request_id, body.accept, rules.party_size)
    except (heroes.HeroError, npcs.NpcError, service.GuildError) as error:
        raise refuse(error) from error
