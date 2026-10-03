"""The town calls: a team's owner says when the team is ready to leave a town, comes back into it, or leaves the party.
Each is its own route. A party comes into a town and is put back together by the map (the service functions), not by a call."""

from fastapi import APIRouter, HTTPException, status

from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules, GameTowns
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..parties.service import PartyError
from . import service

router = APIRouter(prefix="/api/teams/{team_id}/town")


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, (service.TownError, PartyError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def town(team_id: Id, account: CurrentAccount, db: Db) -> dict:
    """Whether the team is in a town with its party, its own state and its party's: who is waiting, and who has told it
    they are ready to leave."""
    try:
        team = await heroes.own_team(db, account, team_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return await service.view(db, team.id)


@router.post("/ready")
async def ready(team_id: Id, account: ActingAccount, db: Db, towns: GameTowns, rules: GameRules) -> dict:
    """Leave Town: the team (with the teams it goes with) is ready to leave and waits for the rest of its party. The others are
    told; when every team is waiting, the party is put back together and its fight starts (``fight`` is its id, or null)."""
    try:
        team = await heroes.own_team(db, account, team_id)
        result, fight = await service.leave(db, towns, rules, team.id)
    except (heroes.HeroError, service.TownError) as error:
        raise refuse(error) from error
    return {"result": result, "fight": fight.id if fight else None, **await service.view(db, team.id)}


@router.post("/come-back")
async def come_back(team_id: Id, account: ActingAccount, db: Db) -> dict:
    """A waiting team goes back into the town: it is no longer waiting to leave."""
    try:
        team = await heroes.own_team(db, account, team_id)
        await service.come_back(db, team.id)
    except (heroes.HeroError, service.TownError) as error:
        raise refuse(error) from error
    return await service.view(db, team.id)


@router.post("/leave-party")
async def leave_party(team_id: Id, account: ActingAccount, db: Db) -> dict:
    """The team leaves its party for good, in the town. If the rest are all waiting, the party is whole again."""
    try:
        team = await heroes.own_team(db, account, team_id)
        await service.leave_party(db, team.id)
    except (heroes.HeroError, service.TownError, PartyError) as error:
        raise refuse(error) from error
    return await service.view(db, team.id)
