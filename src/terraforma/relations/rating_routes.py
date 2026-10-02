"""The rating questions after a fight: list what a player is asked, answer one, or dismiss it. Each is its own route."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRelations
from ..heroes.routes import Id
from . import ratings, service
from .hooks import SCORE_MAX, SCORE_MIN

router = APIRouter(prefix="/api/ratings")


class Rating(Strict):
    score: int = Field(ge=SCORE_MIN, le=SCORE_MAX)


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
    """Rates the other team. The score that results is the game's rule (``Relations.resolve``)."""
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        return await service.view(db, relations, await ratings.answer(db, relations, account.id, prompt_id, body.score))
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
