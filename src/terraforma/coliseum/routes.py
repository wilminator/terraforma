"""The coliseum's calls: ask another team to a bout, answer or take back an offer, and list a team's bouts.

Each call is its own route with a strict body. The calls that change something need a login and the CSRF token (``ActingAccount``) and are rate-limited
per account; the one that only reads needs a login (``CurrentAccount``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from ..heroes import service as heroes
from . import service

router = APIRouter(prefix="/api/teams/{team_id}/coliseum")

TeamId = Annotated[int, Path(ge=1)]
BoutId = Annotated[int, Path(ge=1)]


class Challenge(Strict):
    team_id: int = Field(ge=1)


def refuse(error: Exception) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


@router.get("")
async def listing(team_id: TeamId, account: CurrentAccount, db: Db) -> dict:
    """The team's bouts, asked or asking, newest first."""
    try:
        return {"bouts": [service.view(each) for each in await service.bouts_of(db, account, team_id)]}
    except heroes.NotFound as error:
        raise refuse(error) from error


@router.post("/challenge")
async def challenge(team_id: TeamId, body: Challenge, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """The team asks another team (``team_id`` in the body) to a bout; the offer waits ``Rules.coliseum_offer_seconds``."""
    await limited(request, ratelimit.COLISEUM_BY_ACCOUNT, str(account.id))
    try:
        return {"bout": service.view(await service.challenge(db, rules, account, team_id, body.team_id))}
    except (service.ColiseumError, heroes.NotFound) as error:
        raise refuse(error) from error


@router.post("/{bout_id}/accept")
async def accept(team_id: TeamId, bout_id: BoutId, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """The challenged team's owner takes the bout: the fight starts now."""
    await limited(request, ratelimit.COLISEUM_BY_ACCOUNT, str(account.id))
    try:
        return {"bout": service.view(await service.accept(db, rules, account, bout_id, team_id))}
    except (service.ColiseumError, heroes.NotFound) as error:
        raise refuse(error) from error


@router.post("/{bout_id}/decline")
async def decline(team_id: TeamId, bout_id: BoutId, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.COLISEUM_BY_ACCOUNT, str(account.id))
    try:
        return {"bout": service.view(await service.decline(db, account, bout_id, team_id))}
    except (service.ColiseumError, heroes.NotFound) as error:
        raise refuse(error) from error


@router.post("/{bout_id}/withdraw")
async def withdraw(team_id: TeamId, bout_id: BoutId, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.COLISEUM_BY_ACCOUNT, str(account.id))
    try:
        return {"bout": service.view(await service.withdraw(db, account, bout_id, team_id))}
    except (service.ColiseumError, heroes.NotFound) as error:
        raise refuse(error) from error
