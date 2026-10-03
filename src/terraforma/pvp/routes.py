"""The PvP call: a party picks a fight with another party. One route with a strict model for its arguments; it changes
something, so it needs a login and the CSRF token (``ActingAccount``)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from ..api.deps import ActingAccount, Db, GamePvp, GameRules
from ..heroes import service as heroes
from ..heroes.routes import refuse as refuse_hero
from . import service

router = APIRouter(prefix="/api/pvp")


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class PickFight(Strict):
    """$team_id is one of the caller's teams (its party picks the fight); $party_id is the party it picks it with."""

    team_id: int = Field(ge=1)
    party_id: int = Field(ge=1)


@router.post("/fights", status_code=status.HTTP_201_CREATED)
async def pick_fight(body: PickFight, account: ActingAccount, db: Db, zones: GamePvp, rules: GameRules) -> dict:
    """The party of the caller's team picks a fight with another party: refused with the reason (409) unless both are here, out of a
    town and out of a fight, the place allows PvP and the other party is inside the range window. The fight's id and public
    name are returned; its players command their own heroes as in any fight."""
    try:
        record = await service.start_pvp_fight(db, zones, rules, account, body.team_id, body.party_id)
    except service.PvpError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except heroes.HeroError as error:
        raise refuse_hero(error) from error
    return {"fight": record.id, "guid": record.guid}
