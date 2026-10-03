"""The profile calls. The owner's calls need a login (and, to change anything, the CSRF token); the public pages need
neither, and are limited by the caller's address so a token can't be hunted for."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..alliances import service as alliances
from ..api.deps import ActingAccount, CurrentAccount, Db, GameAlliances, client_address
from ..heroes.routes import Id
from . import service
from .models import BIO_MAX, TOKEN_MAX

router = APIRouter(prefix="/api")

Token = Annotated[str, Path(min_length=8, max_length=TOKEN_MAX, pattern=r"^[A-Za-z0-9_-]+$")]


class Bio(Strict):
    bio: str = Field(max_length=BIO_MAX * 2)


class Directory(Strict):
    enabled: bool


class Visible(Strict):
    team_id: int = Field(ge=1)
    visible: bool


class OnTeam(Strict):
    team_id: int = Field(ge=1)


def refuse(error: service.ProfileError | alliances.AllianceError) -> HTTPException:
    if isinstance(error, (service.NotFound, alliances.NotFound)):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    if isinstance(error, alliances.Forbidden):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(error))
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


FAILURES = (service.ProfileError, alliances.AllianceError)


# --- the owner ----------------------------------------------------------------------------------------------------------------------

@router.get("/profile")
async def my_profile(account: CurrentAccount, db: Db) -> dict:
    return await service.mine(db, account)


@router.post("/profile/token")
async def publish(request: Request, account: ActingAccount, db: Db) -> dict:
    """Makes the player's page, or gives it a new address (the old one stops working)."""
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    await service.publish(db, account)
    return await service.mine(db, account)


@router.put("/profile/bio")
async def set_bio(body: Bio, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    try:
        await service.set_bio(db, account, body.bio)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.mine(db, account)


@router.put("/profile/directory")
async def set_directory(body: Directory, request: Request, account: ActingAccount, db: Db) -> dict:
    """Opts the player in or out of the directory (out until the player says so): in, their handle appears in the rosters of
    their teams' alliances and alliance members can go from a team's page to theirs."""
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    await service.set_directory(db, account, body.enabled)
    return await service.mine(db, account)


@router.put("/profile/team-visible")
async def set_visible(body: Visible, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    try:
        await service.set_visible(db, account, body.team_id, body.visible)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.mine(db, account)


@router.post("/profile/team-token")
async def renew_team(body: OnTeam, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    try:
        await service.renew_team(db, account, body.team_id)
    except FAILURES as error:
        raise refuse(error) from error
    return await service.mine(db, account)


# --- an alliance's page: written by a team whose role may speak for it -------------------------------------------------------------------

@router.get("/alliances/{alliance_id}/profile")
async def alliance_profile(alliance_id: Id, account: CurrentAccount, db: Db) -> dict:
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.reading_team(db, account, alliance)
        return await service.alliance_mine(db, alliance)
    except FAILURES as error:
        raise refuse(error) from error


@router.post("/alliances/{alliance_id}/profile/token")
async def alliance_publish(alliance_id: Id, request: Request, account: ActingAccount, db: Db, rules: GameAlliances) -> dict:
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.speaking_team(db, rules, account, alliance)
        await service.alliance_publish(db, alliance)
        return await service.alliance_mine(db, alliance)
    except FAILURES as error:
        raise refuse(error) from error


@router.put("/alliances/{alliance_id}/profile/bio")
async def alliance_bio(alliance_id: Id, body: Bio, request: Request, account: ActingAccount, db: Db, rules: GameAlliances) -> dict:
    await limited(request, ratelimit.PROFILE_BY_ACCOUNT, str(account.id))
    try:
        alliance = await alliances.get_alliance(db, alliance_id)
        await alliances.speaking_team(db, rules, account, alliance)
        await service.alliance_set_bio(db, alliance, body.bio)
        return await service.alliance_mine(db, alliance)
    except FAILURES as error:
        raise refuse(error) from error


@router.get("/alliances/{alliance_id}/teams/{team_id}/profile")
async def member_team_page(alliance_id: Id, team_id: Id, account: CurrentAccount, db: Db) -> dict:
    """A visible team's page for the members of its alliance (with the player's page, if the player is in the directory)."""
    try:
        return await service.member_team_page(db, account, alliance_id, team_id)
    except FAILURES as error:
        raise refuse(error) from error


# --- the public pages: no login ----------------------------------------------------------------------------------------------------

async def _look(request: Request) -> None:
    await limited(request, ratelimit.PUBLIC_PROFILE_BY_ADDRESS, client_address(request))


@router.get("/p/team/{token}")
async def team_page(request: Request, db: Db, token: Token) -> dict:
    await _look(request)
    try:
        return await service.team_page(db, token)
    except service.ProfileError as error:
        raise refuse(error) from error


@router.get("/p/alliance/{token}")
async def alliance_page(request: Request, db: Db, token: Token) -> dict:
    await _look(request)
    try:
        return await service.alliance_page(db, token)
    except service.ProfileError as error:
        raise refuse(error) from error


@router.get("/p/{token}")
async def player_page(request: Request, db: Db, token: Token) -> dict:
    await _look(request)
    try:
        return await service.player_page(db, token)
    except service.ProfileError as error:
        raise refuse(error) from error
