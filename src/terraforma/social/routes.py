"""The social calls: follows, profile bans and the directory.

Each call is its own route with a strict model. Calls that change something need a login and the CSRF token (``ActingAccount``) and
are rate-limited per account; calls that only read need a login (``CurrentAccount``). The directory search is rate-limited too.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from . import service

router = APIRouter(prefix="/api")


class Who(Strict):
    handle: str = Field(min_length=1, max_length=24)


class ShareBack(Strict):
    handle: str = Field(min_length=1, max_length=24)
    enabled: bool


def refuse(error: service.SocialError) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(error, service.NotFound) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


# --- follows ---------------------------------------------------------------------------------------------------------

@router.get("/follows")
async def my_follows(account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    """Who the caller follows (the most recently active first), how many may be followed, and how many follow the caller."""
    return await service.following(db, rules, account)


@router.post("/follows")
async def follow(body: Who, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await service.follow(db, rules, account, body.handle)
    except service.SocialError as error:
        raise refuse(error) from error
    return await service.following(db, rules, account)


@router.post("/follows/remove")
async def unfollow(body: Who, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await service.unfollow(db, account, body.handle)
    except service.SocialError as error:
        raise refuse(error) from error
    return await service.following(db, rules, account)


@router.put("/follows/share-back")
async def share_back(body: ShareBack, request: Request, account: ActingAccount, db: Db, rules: GameRules) -> dict:
    """Shows (or stops showing) a followed player your handle and profile link. Off until you switch it on."""
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await service.set_share_back(db, account, body.handle, body.enabled)
    except service.SocialError as error:
        raise refuse(error) from error
    return await service.following(db, rules, account)


@router.get("/followers")
async def my_followers(account: CurrentAccount, db: Db) -> dict:
    """How many follow the caller, and the ones who chose to show their handle and profile link."""
    return await service.followers(db, account)


# --- bans ------------------------------------------------------------------------------------------------------------

@router.get("/bans")
async def my_bans(account: CurrentAccount, db: Db) -> dict:
    return {"banned": await service.bans(db, account)}


@router.post("/bans")
async def ban(body: Who, request: Request, account: ActingAccount, db: Db) -> dict:
    """Bars a player from the caller's profile pages. It changes nothing in a game."""
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await service.ban(db, account, body.handle)
    except service.SocialError as error:
        raise refuse(error) from error
    return {"banned": await service.bans(db, account)}


@router.post("/bans/remove")
async def unban(body: Who, request: Request, account: ActingAccount, db: Db) -> dict:
    await limited(request, ratelimit.SOCIAL_BY_ACCOUNT, str(account.id))
    try:
        await service.unban(db, account, body.handle)
    except service.SocialError as error:
        raise refuse(error) from error
    return {"banned": await service.bans(db, account)}


# --- the directory ---------------------------------------------------------------------------------------------------

@router.get("/directory")
async def directory(request: Request, account: CurrentAccount, db: Db, prefix: Annotated[str, Query(min_length=1, max_length=24)]) -> dict:
    """Players who opted in whose handle starts with $prefix: handle, bio, whether the caller follows them, and a profile link if they switched it on."""
    await limited(request, ratelimit.DIRECTORY_BY_ACCOUNT, str(account.id))
    return {"players": await service.search(db, account, prefix)}
