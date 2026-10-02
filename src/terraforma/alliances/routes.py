"""The alliance calls: found one, invite, join, leave, run it, and speak for it in relationships. Each is its own route.

A team's owner acts for the team (``team_id`` in the body says which of the account's teams). What a role may do is the
game's rule (``Alliances.allowed``); an account with no team in an alliance cannot tell that it exists."""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, GameRelations
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..relations import service as relations
from ..relations.hooks import Change, Ref
from ..relations.routes import Other, SetRelationship
from ..relations.routes import refuse as refuse_relation
from . import service
from .models import NAME_MAX

router = APIRouter(prefix="/api")


class Found(Strict):
    team_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=NAME_MAX * 2)


class Acting(Strict):
    team_id: int = Field(ge=1)


class OnTeam(Acting):
    target_team_id: int = Field(ge=1)


class SetRole(OnTeam):
    role: str = Field(min_length=1, max_length=16)


class Answer(Strict):
    alliance_id: int = Field(ge=1)


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, service.NotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, service.Forbidden):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    if isinstance(error, service.AllianceError):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    if isinstance(error, relations.RelationError):
        return refuse_relation(error)
    return refuse_hero(error)


FAILURES = (heroes.HeroError, service.AllianceError, relations.RelationError)


@router.get("/alliances")
async def my_alliances(account: CurrentAccount, db: Db) -> dict:
    return await service.mine(db, account)


@router.post("/alliances", status_code=status.HTTP_201_CREATED)
async def found(body: Found, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.ALLIANCE_BY_ACCOUNT, str(account.id))
    try:
        team = await heroes.own_team(db, account, body.team_id)
        alliance = await service.found(db, alliances, team, body.name)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.get("/alliances/{alliance_id}")
async def look(alliance_id: Id, account: CurrentAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance = await service.get_alliance(db, alliance_id)
        member = await service.reading_team(db, account, alliance)
        return await service.view(db, alliances, alliance, member.team_id)
    except FAILURES as error:
        raise refuse(error) from error


async def _act(db, account, alliance_id: int, team_id: int):
    """The alliance and the acting team, which must be the account's own."""
    team = await heroes.own_team(db, account, team_id)
    return await service.get_alliance(db, alliance_id), team


@router.post("/alliances/{alliance_id}/invite")
async def invite(alliance_id: Id, body: OnTeam, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.ALLIANCE_BY_ACCOUNT, str(account.id))
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.invite(db, alliances, alliance, team.id, body.target_team_id)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/withdraw")
async def withdraw(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.withdraw(db, alliances, alliance, team.id, body.target_team_id)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/leave")
async def leave(alliance_id: Id, body: Acting, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        return {"disbanded": await service.leave(db, alliances, alliance, team.id)}
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/remove")
async def remove(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.remove(db, alliances, alliance, team.id, body.target_team_id)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/role")
async def role(alliance_id: Id, body: SetRole, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.set_role(db, alliances, alliance, team.id, body.target_team_id, body.role)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/hand-over")
async def hand_over(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.hand_over(db, alliances, alliance, team.id, body.target_team_id)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/disband")
async def disband(alliance_id: Id, body: Acting, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        alliance, team = await _act(db, account, alliance_id, body.team_id)
        await service.disband(db, alliances, alliance, team.id)
        return {"disbanded": True}
    except FAILURES as error:
        raise refuse(error) from error


@router.get("/teams/{team_id}/invitations")
async def invitations(team_id: Id, account: CurrentAccount, db: Db) -> list[dict]:
    try:
        team = await heroes.own_team(db, account, team_id)
    except FAILURES as error:
        raise refuse(error) from error
    return [row for row in (await service.mine(db, account))["invitations"] if row["team_id"] == team.id]


@router.post("/teams/{team_id}/invitations/accept")
async def accept(team_id: Id, body: Answer, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        team = await heroes.own_team(db, account, team_id)
        alliance = await service.accept(db, alliances, team, body.alliance_id)
        return await service.view(db, alliances, alliance, team.id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/teams/{team_id}/invitations/decline")
async def decline(team_id: Id, body: Answer, account: ActingAccount, db: Db) -> dict:
    try:
        team = await heroes.own_team(db, account, team_id)
        await service.decline(db, team, body.alliance_id)
    except FAILURES as error:
        raise refuse(error) from error
    return {"declined": True}


# --- the alliance's own relationships: private to it, set by a role that may speak for it ----------------------------------------------

@router.get("/alliances/{alliance_id}/relationships")
async def relationships(alliance_id: Id, account: CurrentAccount, db: Db, alliances: GameAlliances, relating: GameRelations) -> dict:
    try:
        alliance = await service.get_alliance(db, alliance_id)
        await service.reading_team(db, account, alliance)
        return {"bands": [{"name": band.name, "low": band.low, "high": band.high} for band in relating.bands],
                "relationships": await relations.list_for(db, relating, Ref("alliance", alliance.id))}
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/relationships/set")
async def set_relationship(alliance_id: Id, body: SetRelationship, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances, relating: GameRelations) -> dict:
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        alliance = await service.get_alliance(db, alliance_id)
        await service.speaking_team(db, alliances, account, alliance)
        subject, other = Ref("alliance", alliance.id), Ref(body.kind, body.id)
        row = None
        if body.score is not None:
            row = await relations.apply(db, relating, Change(subject, other, score=body.score, by="player"))
        if body.note is not None:
            row = await relations.set_note(db, relating, subject, other, body.note)
        return await relations.view(db, relating, row)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/relationships/forget")
async def forget_relationship(alliance_id: Id, body: Other, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.RELATION_BY_ACCOUNT, str(account.id))
    try:
        alliance = await service.get_alliance(db, alliance_id)
        await service.speaking_team(db, alliances, account, alliance)
        return {"forgotten": await relations.drop(db, Ref("alliance", alliance.id), Ref(body.kind, body.id))}
    except FAILURES as error:
        raise refuse(error) from error
