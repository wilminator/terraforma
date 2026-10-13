"""The fight page calls.

A team's history and the fights a player can view need a login (``CurrentAccount``); they only read. The public pages need
none and are limited by the caller's address, like the public profile pages. Each call is its own route; a number or a public name
that doesn't fit is refused before any game code runs.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import CurrentAccount, Db, GameRules, client_address
from ..fighttags.scoring import KINDS
from ..heroes.models import Team
from ..heroes.routes import Id
from . import service

router = APIRouter(prefix="/api")

Before = Annotated[int | None, Query(ge=1)]
Limit = Annotated[int, Query(ge=1, le=50)]
Guid = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]
Kind = Annotated[str | None, Query(pattern="^(" + "|".join(KINDS) + ")$")]


def refuse(error: service.PageError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, str(error))


async def _look(request: Request) -> None:
    await limited(request, ratelimit.PUBLIC_PROFILE_BY_ADDRESS, client_address(request))


# --- for the signed-in ---------------------------------------------------------------------------------------------------

@router.get("/teams/{team_id}/fights")
async def team_fights(team_id: Id, account: CurrentAccount, db: Db, before: Before = None, limit: Limit = 20) -> list[dict]:
    """The team's finished fights, newest first, for the team's owner: how each went and whether the team keeps it."""
    team = await db.get(Team, team_id)
    if team is None or team.account_id != account.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such team")
    return await service.team_history(db, team_id, before, limit)


@router.get("/fight-pages/viewable")
async def viewable(account: CurrentAccount, db: Db, before: Before = None, limit: Limit = 20) -> list[dict]:
    """The fights the caller can view, newest first, each with why: their own, those of players they follow, public ones and their alliances'."""
    return await service.viewable(db, account.id, before, limit)


@router.get("/fight-pages/{fight_id}/replay")
async def viewable_replay(fight_id: Id, account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """A fight the caller can view, as it stands with every round played, to play it back."""
    try:
        return await service.viewable_replay(db, rules, account.id, fight_id)
    except service.PageError as error:
        raise refuse(error) from error


# --- for anyone --------------------------------------------------------------------------------------------------------------

@router.get("/public/fights/showcase")
async def showcase(request: Request, db: Db, rules: GameRules, kind: Kind = None) -> dict:
    """A random best fight (underdog or overkill) of a team its player made visible."""
    await _look(request)
    try:
        return await service.showcase(db, rules, kind)
    except service.PageError as error:
        raise refuse(error) from error


@router.get("/public/fights/coliseum")
async def coliseum(request: Request, db: Db, before: Before = None, limit: Limit = 20) -> list[dict]:
    """Every public fight, newest first."""
    await _look(request)
    return await service.coliseum(db, before, limit)


@router.get("/public/fights/{guid}/replay")
async def public_replay(guid: Guid, request: Request, db: Db, rules: GameRules) -> dict:
    """A public fight (or a visible team's best fight) by its public name."""
    await _look(request)
    try:
        return await service.public_replay(db, rules, guid)
    except service.PageError as error:
        raise refuse(error) from error
