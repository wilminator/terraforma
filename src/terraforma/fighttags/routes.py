"""The fight tag calls: a team's tags and the fights it keeps, an alliance's top lists and the fights it keeps.

Each call is its own route with a strict model. Calls that change something need a login and the CSRF token (``ActingAccount``)
and are rate-limited per account; calls that only read need a login (``CurrentAccount``). A team's calls are for its owner. An
alliance's are for its member teams' owners (anyone to look; only a team whose role may speak for the alliance to change).
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..alliances import service as alliances
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, GameRules
from ..heroes.models import Team
from ..heroes.routes import Id
from . import service

router = APIRouter(prefix="/api")


class Fight(Strict):
    fight_id: int = Field(ge=1)


class TeamFight(Strict):
    team_id: int = Field(ge=1)
    fight_id: int = Field(ge=1)


def refuse(error: service.TagError | alliances.AllianceError) -> HTTPException:
    if isinstance(error, (service.NotFound, alliances.NotFound)):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, alliances.Forbidden):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


FAILURES = (service.TagError, alliances.AllianceError)


async def _mine(db, account, team_id: int) -> Team:
    team = await db.get(Team, team_id)
    if team is None or team.account_id != account.id:
        raise service.NotFound("there's no such team")
    return team


# --- a team ----------------------------------------------------------------------------------------------------------

@router.get("/teams/{team_id}/fight-tags")
async def team_tags(team_id: Id, account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """The team's four tags (the best fight of each kind), the fights it keeps by hand and how many it may."""
    try:
        await _mine(db, account, team_id)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.team_view(db, rules, team_id)


@router.post("/teams/{team_id}/kept")
async def keep(team_id: Id, body: Fight, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Keeps a finished fight the team was in, in one of its slots."""
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await _mine(db, account, team_id)
        await service.keep(db, rules, team_id, body.fight_id)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.team_view(db, rules, team_id)


@router.post("/teams/{team_id}/kept/remove")
async def release(team_id: Id, body: Fight, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await _mine(db, account, team_id)
    except FAILURES as error:
        raise refuse(error) from error
    await service.release(db, team_id, body.fight_id)
    return await service.team_view(db, rules, team_id)


# --- an alliance -------------------------------------------------------------------------------------------------------

@router.get("/alliances/{alliance_id}/fight-tags")
async def alliance_tags(alliance_id: Id, account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """The alliance's best fights of each kind, the fights it keeps by hand and the sizes. For the owners of its teams."""
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.reading_team(db, account, alliance)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.alliance_view(db, rules, alliance_id)


@router.post("/alliances/{alliance_id}/kept")
async def alliance_keep(alliance_id: Id, body: TeamFight, request: Request, account: ActingAccount, db: Db, rules: GameRules, hooks: GameAlliances) -> dict:
    """Keeps a finished fight that one of the caller's teams in the alliance was in, in one of the alliance's slots."""
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.speaking_team(db, hooks, account, alliance)
        team = await _mine(db, account, body.team_id)
        if await alliances.member_row(db, alliance.id, team.id) is None:
            raise service.NotFound("that team isn't in this alliance")
        await service.alliance_keep(db, rules, alliance.id, team.id, body.fight_id)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.alliance_view(db, rules, alliance_id)


@router.post("/alliances/{alliance_id}/kept/remove")
async def alliance_release(alliance_id: Id, body: Fight, request: Request, account: ActingAccount, db: Db, rules: GameRules, hooks: GameAlliances) -> dict:
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.speaking_team(db, hooks, account, alliance)
    except FAILURES as error:
        raise refuse(error) from error
    await service.alliance_release(db, alliance_id, body.fight_id)
    return await service.alliance_view(db, rules, alliance_id)
