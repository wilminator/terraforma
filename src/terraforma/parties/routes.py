"""The party calls. A team enters the game through its own route: when its player selects it to play, it is in a party. Joining
and merging parties are service functions the map drives, not calls."""

from fastapi import APIRouter, HTTPException, status

from ..api.deps import ActingAccount, Db, GameRules
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/teams/{team_id}")


@router.post("/play")
async def play(team_id: Id, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """The team enters the game: a party of just that team is made where the team's first hero stands (a team already in a party
    keeps it: safe to repeat). Refused (409) for a team with no heroes."""
    try:
        team = await heroes.own_team(db, account, team_id)
        party = await service.play(db, team.id, rules.party_size)
    except heroes.HeroError as error:
        raise refuse_hero(error) from error
    except service.PartyError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    return {"party": party.id, "teams": await service.team_ids(db, party.id), "map_id": party.map_id, "x": party.x, "y": party.y}
