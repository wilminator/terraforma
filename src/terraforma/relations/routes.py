"""The relationship calls: a team's owner reads and sets how the team feels about others. Each is its own route.

What a team's relationships say is private to it: only the owner reads them, and the other side never sees a score or a note."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field, model_validator

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRelations
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from . import service
from .hooks import SCORE_MAX, SCORE_MIN, Change, Ref
from .models import NOTE_MAX

router = APIRouter(prefix="/api/teams/{team_id}/relationships")

Kind = Literal["team"]


class Other(Strict):
    kind: Kind = "team"
    id: int = Field(ge=1)


class SetRelationship(Other):
    score: int | None = Field(default=None, ge=SCORE_MIN, le=SCORE_MAX)
    note: str | None = Field(default=None, max_length=NOTE_MAX)

    @model_validator(mode="after")
    def something_to_set(self):
        if self.score is None and self.note is None:
            raise ValueError("set a score, a note or both")
        return self


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.RelationError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def relationships(team_id: Id, account: CurrentAccount, db: Db, relations: GameRelations) -> dict:
    try:
        team = await heroes.own_team(db, account, team_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return {"bands": [{"name": band.name, "low": band.low, "high": band.high} for band in relations.bands],
            "relationships": await service.list_for(db, relations, Ref("team", team.id))}


@router.post("/set")
async def set_relationship(team_id: Id, body: SetRelationship, request: Request, account: ActingAccount, db: Db, relations: GameRelations) -> dict:
    """Sets the team's score for another team and/or its private note. The score that results is the game's rule."""
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        team = await heroes.own_team(db, account, team_id)
        subject, other = Ref("team", team.id), Ref(body.kind, body.id)
        row = None
        if body.score is not None:
            row = await service.apply(db, relations, Change(subject, other, score=body.score, by="player"))
        if body.note is not None:
            row = await service.set_note(db, relations, subject, other, body.note)
        return await service.view(db, relations, row)
    except (heroes.HeroError, service.RelationError) as error:
        raise refuse(error) from error


@router.post("/forget")
async def forget_relationship(team_id: Id, body: Other, request: Request, account: ActingAccount, db: Db) -> dict:
    """The team lets go of what it thinks of another team (score and note)."""
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        team = await heroes.own_team(db, account, team_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return {"forgotten": await service.drop(db, Ref("team", team.id), Ref(body.kind, body.id))}
