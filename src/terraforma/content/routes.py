"""The admin's monster calls: look at the game's monsters, write new ones, change and retire the ones written here (``content.authoring``).

Each call is its own route with a strict model. The calls that change something depend on ``AdminAccount`` (login, CSRF token and ``is_admin``)
and are rate-limited per account; the ones that only read need a login and ``is_admin`` (``CurrentAccount``). The seed's monsters can be read but are changed in the seed.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.common import Strict, limited
from ..api.deps import AdminAccount, CurrentAccount, Db, GameRules
from . import authoring

router = APIRouter(prefix="/api/admin/monsters")

MonsterKey = Annotated[str, Path(min_length=1, max_length=64)]
Keys = Annotated[list[str], Field(max_length=64)]


class MonsterBody(Strict):
    """A monster as monsters.json writes it (the key is the path's). The values are checked against the game's stats and content."""

    name: str = Field(min_length=1, max_length=64)
    personality: str = Field(min_length=1, max_length=64)
    xp_reward: int = Field(default=0, ge=0, le=10**9)
    gold_reward: int = Field(default=0, ge=0, le=10**9)
    stats: dict[str, int] = Field(default_factory=dict, max_length=32)
    abilities: Keys = []
    items: Keys = []
    equipment: Keys = []
    drops: Keys = []
    ai: dict[str, int] = Field(default_factory=dict, max_length=8)


class CreateBody(MonsterBody):
    key: str = Field(min_length=1, max_length=64)


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "administrators only")


def refuse(error: authoring.AuthoringError) -> HTTPException:
    if isinstance(error, authoring.NoSuchMonster):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(error))
    code = status.HTTP_409_CONFLICT if isinstance(error, authoring.Conflict) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


@router.get("")
async def monsters(account: CurrentAccount, db: Db, rules: GameRules, authored: bool | None = None, active: bool | None = None,
                   after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    """The game's monsters, oldest first after the id $after; ``authored`` narrows to the admins' (or the seed's) and ``active`` to those in play (or retired)."""
    only_admin(account)
    return {"monsters": await authoring.views(db, rules, await authoring.listing(db, authored, active, after, limit))}


@router.get("/{key}")
async def monster(key: MonsterKey, account: CurrentAccount, db: Db, rules: GameRules) -> dict:
    only_admin(account)
    try:
        return {"monster": (await authoring.views(db, rules, [await authoring.get(db, key)]))[0]}
    except authoring.AuthoringError as error:
        raise refuse(error) from error


@router.post("")
async def create(body: CreateBody, request: Request, account: AdminAccount, db: Db, rules: GameRules) -> dict:
    """Writes a new monster (``authored``: loading the seed never retires it). The key must be new."""
    await limited(request, ratelimit.MONSTER_AUTHOR_BY_ACCOUNT, str(account.id))
    try:
        row = await authoring.create(db, rules, body.key, body.model_dump(exclude={"key"}))
        return {"monster": (await authoring.views(db, rules, [row]))[0]}
    except authoring.AuthoringError as error:
        raise refuse(error) from error


@router.put("/{key}")
async def replace(key: MonsterKey, body: MonsterBody, request: Request, account: AdminAccount, db: Db, rules: GameRules) -> dict:
    """Rewrites a monster an admin wrote, whole. One from the seed can't be changed here."""
    await limited(request, ratelimit.MONSTER_AUTHOR_BY_ACCOUNT, str(account.id))
    try:
        row = await authoring.replace(db, rules, key, body.model_dump())
        return {"monster": (await authoring.views(db, rules, [row]))[0]}
    except authoring.AuthoringError as error:
        raise refuse(error) from error


@router.post("/{key}/retire")
async def retire(key: MonsterKey, request: Request, account: AdminAccount, db: Db) -> dict:
    """Takes an authored monster out of play (it is kept, as fights and quests may still name it)."""
    await limited(request, ratelimit.MONSTER_AUTHOR_BY_ACCOUNT, str(account.id))
    try:
        return {"key": key, "active": (await authoring.set_active(db, key, False)).active}
    except authoring.AuthoringError as error:
        raise refuse(error) from error


@router.post("/{key}/restore")
async def restore(key: MonsterKey, request: Request, account: AdminAccount, db: Db) -> dict:
    """Brings a retired authored monster back."""
    await limited(request, ratelimit.MONSTER_AUTHOR_BY_ACCOUNT, str(account.id))
    try:
        return {"key": key, "active": (await authoring.set_active(db, key, True)).active}
    except authoring.AuthoringError as error:
        raise refuse(error) from error
