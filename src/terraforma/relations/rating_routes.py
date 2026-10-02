"""The rating questions after a fight: list what a player is asked, answer one, or dismiss it. Each is its own route."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field, model_validator

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRelations
from ..heroes.routes import Id
from . import ratings, service
from .hooks import SCORE_MAX, SCORE_MIN

router = APIRouter(prefix="/api/ratings")


class Rating(Strict):
    """A score for the other team, or ``accept`` for the change the game's rule suggested (one or the other)."""

    score: int | None = Field(default=None, ge=SCORE_MIN, le=SCORE_MAX)
    accept: bool = False

    @model_validator(mode="after")
    def one_answer(self):
        if self.accept == (self.score is not None):
            raise ValueError("answer with a score, or accept the suggestion")
        return self


def refuse(error: service.RelationError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(error, service.NotFound) else status.HTTP_409_CONFLICT
    return HTTPException(code, str(error))


@router.get("")
async def questions(account: CurrentAccount, db: Db, relations: GameRelations) -> dict:
    """What the caller's teams are asked: another player team helped or harmed them in a fight, and they have no opinion of it."""
    return {"bands": [{"name": band.name, "low": band.low, "high": band.high} for band in relations.bands],
            "questions": await ratings.pending(db, account.id)}


@router.post("/{prompt_id}/answer")
async def answer(prompt_id: Id, body: Rating, request: Request, account: ActingAccount, db: Db, relations: GameRelations) -> dict:
    """Rates the other team, or accepts the change the game's rule suggested. The score that results is the game's rule (``Relations.resolve``)."""
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        return await service.view(db, relations, await ratings.answer(db, relations, account.id, prompt_id, body.score, body.accept))
    except service.RelationError as error:
        raise refuse(error) from error


@router.post("/{prompt_id}/dismiss")
async def dismiss(prompt_id: Id, request: Request, account: ActingAccount, db: Db) -> dict:
    """Closes the question without rating."""
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        await ratings.dismiss(db, account.id, prompt_id)
    except service.RelationError as error:
        raise refuse(error) from error
    return {"dismissed": True}
