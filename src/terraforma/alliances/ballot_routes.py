"""The ballot calls: put a question to the alliance, vote, close, and read them. Each is its own route.

A team's owner acts for the team (``team_id`` says which of the account's teams votes or opens). Reading is for any member
team's owner; someone with no team in the alliance gets a 404, as for the alliance itself."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances
from ..heroes import service as heroes
from ..heroes.routes import Id
from . import ballots, service
from .routes import FAILURES, Acting, refuse as refuse_alliance

router = APIRouter(prefix="/api/alliances/{alliance_id}/ballots")


class OpenBallot(Acting):
    title: str = Field(min_length=1, max_length=ballots.TITLE_MAX * 2)
    options: list[Annotated[str, Field(min_length=1, max_length=ballots.OPTION_MAX * 2)]] = Field(min_length=2, max_length=20)
    kind: str = Field(default="", max_length=ballots.KIND_MAX)
    payload: dict | None = None
    secret: bool = False
    closes_in_hours: int | None = Field(default=None, ge=1, le=720)


class Vote(Acting):
    option: int = Field(ge=0, le=19)


def refuse(error: ValueError) -> HTTPException:
    return refuse_alliance(error)


async def viewer(db, account, alliance, team_id: int | None) -> int:
    """The account's team the ballots are read as: the one asked for, which must be a member, or the first of its member teams."""
    if team_id is not None:
        team = await heroes.own_team(db, account, team_id)
        if await service.member_row(db, alliance.id, team.id) is None:
            raise service.NotFound("there's no such alliance")
        return team.id
    return (await service.reading_team(db, account, alliance)).team_id


@router.get("")
async def my_ballots(alliance_id: Id, account: CurrentAccount, db: Db, alliances: GameAlliances, team_id: Annotated[int | None, Query(ge=1)] = None) -> list[dict]:
    try:
        alliance = await service.get_alliance(db, alliance_id)
        return await ballots.listing(db, alliances, alliance, await viewer(db, account, alliance, team_id))
    except FAILURES as error:
        raise refuse(error) from error


@router.post("", status_code=status.HTTP_201_CREATED)
async def open_ballot(alliance_id: Id, body: OpenBallot, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.ALLIANCE_BY_ACCOUNT, str(account.id))
    try:
        team = await heroes.own_team(db, account, body.team_id)
        alliance = await service.get_alliance(db, alliance_id)
        closes_in = None if body.closes_in_hours is None else body.closes_in_hours * 3600
        ballot = await ballots.open_ballot(
            db, alliances, alliance, team.id, body.title, body.options, kind=body.kind, payload=body.payload, secret=body.secret, closes_in=closes_in,
        )
        return await ballots.view(db, alliances, alliance, ballot, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.get("/{ballot_id}")
async def look(alliance_id: Id, ballot_id: Id, account: CurrentAccount, db: Db, alliances: GameAlliances, team_id: Annotated[int | None, Query(ge=1)] = None) -> dict:
    try:
        alliance = await service.get_alliance(db, alliance_id)
        seen_as = await viewer(db, account, alliance, team_id)
        return await ballots.view(db, alliances, alliance, await ballots.get_ballot(db, alliance, ballot_id), seen_as)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/{ballot_id}/vote")
async def vote(alliance_id: Id, ballot_id: Id, body: Vote, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.ALLIANCE_BY_ACCOUNT, str(account.id))
    try:
        team = await heroes.own_team(db, account, body.team_id)
        alliance = await service.get_alliance(db, alliance_id)
        ballot = await ballots.cast(db, alliances, alliance, team.id, await ballots.get_ballot(db, alliance, ballot_id), body.option)
        return await ballots.view(db, alliances, alliance, ballot, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/{ballot_id}/close")
async def close(alliance_id: Id, ballot_id: Id, body: Acting, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        team = await heroes.own_team(db, account, body.team_id)
        alliance = await service.get_alliance(db, alliance_id)
        ballot = await ballots.close(db, alliances, alliance, team.id, await ballots.get_ballot(db, alliance, ballot_id))
        return await ballots.view(db, alliances, alliance, ballot, team.id)
    except FAILURES as error:
        raise refuse(error) from error
