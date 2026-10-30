"""The saved maps calls, all for admins: look over the maps a game generated and kept for reuse (``maps.library``), approve one to let it be
copied, reject one, or drop one.

Each call is its own route. Calls that change something need a login and the CSRF token (``ActingAccount``) and are rate-limited per account;
the call that only reads needs a login (``CurrentAccount``).
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, status

from ..accounts import ratelimit
from ..accounts.routes import limited
from ..api.deps import ActingAccount, CurrentAccount, Db
from ..world.start import ensure_start
from . import library

router = APIRouter(prefix="/api/admin")

SavedMapId = Annotated[int, Path(ge=1)]


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may look at the saved maps")


def refuse(error: library.LibraryError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND if "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT, str(error))


@router.get("/saved-maps")
async def listing(account: CurrentAccount, db: Db) -> dict:
    """Every saved map of the world, by key."""
    only_admin(account)
    world_id = (await ensure_start(db)).world_id
    return {"saved_maps": [library.view(each) for each in await library.listing(db, world_id)]}


async def decide(action, saved_id: int, request: Request, account, db) -> dict:
    only_admin(account)
    await limited(request, ratelimit.SAVED_MAP_BY_ACCOUNT, str(account.id))
    try:
        return {"saved_map": library.view(await action(db, saved_id))}
    except library.LibraryError as error:
        raise refuse(error) from error


@router.post("/saved-maps/{saved_id}/approve")
async def approve(saved_id: SavedMapId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Lets a proposed saved map be copied for later places."""
    return await decide(lambda db, each: library.review(db, each, True), saved_id, request, account, db)


@router.post("/saved-maps/{saved_id}/reject")
async def reject(saved_id: SavedMapId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Shuts a proposed saved map out: it is never copied."""
    return await decide(lambda db, each: library.review(db, each, False), saved_id, request, account, db)


@router.post("/saved-maps/{saved_id}/forget")
async def forget(saved_id: SavedMapId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Drops a saved map (maps made from it stay); the game may propose the key again."""
    return await decide(library.forget_by_id, saved_id, request, account, db)
