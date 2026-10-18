"""The alliance calls: found one, invite, join, leave, run it, and speak for it in relationships. Each is its own route.

A team's owner acts for the team (``team_id`` in the body says which of the account's teams). What a role may do is the
game's rule (``Alliances.allowed``); an account with no team in an alliance cannot tell that it exists."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
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
from . import ballots, service
from .hooks import ACTIONS
from .models import NAME_MAX, ROLE_MAX, Alliance

router = APIRouter(prefix="/api")


class Found(Strict):
    team_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=NAME_MAX * 2)


class Acting(Strict):
    team_id: int = Field(ge=1)


class Governed(Acting):
    """An action the alliance's rules may ask a vote for: ``vote`` puts it to a ballot even when no rule does, which closes in ``closes_in_hours`` (the game's default if not given)."""

    vote: bool = False
    closes_in_hours: int | None = Field(default=None, ge=1, le=720)


class OnTeam(Governed):
    target_team_id: int = Field(ge=1)


class Appoint(OnTeam):
    role: str = Field(min_length=1, max_length=ROLE_MAX * 2)


class DefineRole(Governed):
    name: str = Field(min_length=1, max_length=ROLE_MAX * 2)
    actions: list[Annotated[str, Field(min_length=1, max_length=32)]] = Field(max_length=len(ACTIONS))


class RoleName(Governed):
    name: str = Field(min_length=1, max_length=ROLE_MAX * 2)


class SetRule(Governed):
    action: str = Field(min_length=1, max_length=24)
    role: str = Field(default="", max_length=ROLE_MAX * 2)
    percent: int | None = Field(ge=1, le=100)


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
async def look(alliance_id: Id, account: CurrentAccount, db: Db, alliances: GameAlliances, team_id: Annotated[int | None, Query(ge=1)] = None) -> dict:
    """The alliance as one of the account's member teams sees it: the one asked for with ``?team_id=`` (it must be a member), else the first."""
    try:
        alliance = await service.get_alliance(db, alliance_id)
        if team_id is None:
            seen_as = (await service.reading_team(db, account, alliance)).team_id
        else:
            seen_as = (await heroes.own_team(db, account, team_id)).id
            if await service.member_row(db, alliance.id, seen_as) is None:
                raise service.NotFound("there's no such alliance")
        return await service.view(db, alliances, alliance, seen_as)
    except FAILURES as error:
        raise refuse(error) from error


async def _act(db, account, alliance_id: int, team_id: int):
    """The alliance and the acting team, which must be the account's own."""
    team = await heroes.own_team(db, account, team_id)
    return await service.get_alliance(db, alliance_id), team


async def _do(db, alliances, account, alliance_id: int, body: Governed, run: str, **args) -> dict:
    """Does the action, or puts it to a ballot (the answer is then ``{"ballot": ...}``) when the alliance's rules or the caller ask for a vote."""
    alliance, team = await _act(db, account, alliance_id, body.team_id)
    closes_in = None if body.closes_in_hours is None else body.closes_in_hours * 3600
    ballot = await ballots.perform(db, alliances, alliance, team.id, run, args, vote=body.vote, closes_in=closes_in)
    if await db.get(Alliance, alliance.id) is None:  # (it disbanded, now or by the vote)
        return {"disbanded": True}
    if ballot is None:
        return await service.view(db, alliances, alliance, team.id)
    return {"ballot": await ballots.view(db, alliances, alliance, ballot, team.id)}


@router.post("/alliances/{alliance_id}/invite")
async def invite(alliance_id: Id, body: OnTeam, request: Request, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    await limited(request, ratelimit.ALLIANCE_BY_ACCOUNT, str(account.id))
    try:
        return await _do(db, alliances, account, alliance_id, body, "invite", team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/withdraw")
async def withdraw(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "withdraw", team_id=body.target_team_id)
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
        return await _do(db, alliances, account, alliance_id, body, "remove", target_team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/appoint")
async def appoint(alliance_id: Id, body: Appoint, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "appoint", target_team_id=body.target_team_id, role=body.role)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/revoke")
async def revoke(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "revoke", target_team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/roles")
async def define_role(alliance_id: Id, body: DefineRole, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "define_role", name=body.name, actions=body.actions)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/roles/delete")
async def delete_role(alliance_id: Id, body: RoleName, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "delete_role", name=body.name)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/hand-over")
async def hand_over(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "hand_over", target_team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/disband")
async def disband(alliance_id: Id, body: Governed, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "disband")
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/rules")
async def set_rule(alliance_id: Id, body: SetRule, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    """Asks for a vote (``percent`` of the active teams) when a team in ``role`` (empty: any) does ``action``; ``percent`` null drops the requirement."""
    try:
        return await _do(db, alliances, account, alliance_id, body, "set_rule", action=body.action, role=body.role, percent=body.percent)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/elect-subcommander")
async def elect_subcommander(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    """A team of tier 3 puts it to all the members to make a team a subcommander (always a ballot)."""
    try:
        return await _do(db, alliances, account, alliance_id, body, "elect_subcommander", target_team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/dismiss-subcommander")
async def dismiss_subcommander(alliance_id: Id, body: OnTeam, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    try:
        return await _do(db, alliances, account, alliance_id, body, "dismiss_subcommander", target_team_id=body.target_team_id)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/remove-commander")
async def remove_commander(alliance_id: Id, body: Governed, account: ActingAccount, db: Db, alliances: GameAlliances) -> dict:
    """A subcommander puts it to the subcommanders to remove the commander (always a ballot; all of the active subcommanders must agree)."""
    try:
        return await _do(db, alliances, account, alliance_id, body, "remove_commander")
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
