"""The weekly quest calls: a player's team asks for its quest of the week, and an admin looks over the generated ones and promotes them.

Each call is its own route with a strict model. Calls that change something need a login and the CSRF token (``ActingAccount``) and are
rate-limited per account; calls that only read need a login (``CurrentAccount``). Starting and finishing a quest is the game's (the
dungeon and its dialog call ``quests.weekly.start`` and ``finish``), so there is no call for it here.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import Field

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameWeeklyQuests
from ..heroes import service as heroes
from . import weekly

router = APIRouter(prefix="/api")

TeamId = Annotated[int, Path(ge=1)]
QuestId = Annotated[int, Path(ge=1)]


class Promote(Strict):
    key: str = Field(min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    min_level: int | None = Field(default=None, ge=1, le=weekly.MAX_LEVEL)
    max_level: int | None = Field(default=None, ge=1, le=weekly.MAX_LEVEL)
    weight: int = Field(default=1, ge=1, le=1000)
    spec: dict | None = None


def refuse(error: Exception) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(error, heroes.NotFound) or "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may look at generated quests")


@router.get("/teams/{team_id}/weekly-quest")
async def weekly_quest(team_id: TeamId, account: CurrentAccount, db: Db) -> dict:
    """The team's quest of the week, if it has been offered one (``quest`` is null otherwise: ask with the offer call)."""
    try:
        await heroes.own_team(db, account, team_id)
    except heroes.NotFound as error:
        raise refuse(error) from error
    quest = await weekly.current(db, team_id)
    return {"quest": weekly.view(quest) if quest else None}


@router.post("/teams/{team_id}/weekly-quest/offer")
async def offer(team_id: TeamId, request: Request, account: ActingAccount, db: Db, hooks: GameWeeklyQuests) -> dict:
    """The team's quest of the week: the one it already has, or one drawn now from a template or generated. ``quest`` is null when the game
    makes none for this team this week."""
    await limited(request, ratelimit.WEEKLY_QUEST_BY_ACCOUNT, str(account.id))
    try:
        await heroes.own_team(db, account, team_id)
        quest = await weekly.offer(db, hooks, team_id)
    except (heroes.NotFound, weekly.WeeklyError) as error:
        raise refuse(error) from error
    return {"quest": weekly.view(quest) if quest else None}


@router.get("/admin/weekly-quests")
async def generated(account: CurrentAccount, db: Db, promoted: bool | None = None, after: Annotated[int, Query(ge=0)] = 0,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    """The recorded generated quests, oldest first after the id $after (an admin only); ``promoted`` narrows to those already made templates, or not yet."""
    only_admin(account)
    return {"quests": [weekly.admin_view(quest) for quest in await weekly.generated(db, promoted, after, limit)]}


@router.post("/admin/weekly-quests/{quest_id}/promote")
async def promote(quest_id: QuestId, body: Promote, request: Request, account: ActingAccount, db: Db) -> dict:
    """Makes a generated quest a permanent template (an admin only), as it was or with tweaks to its name, levels, weight and spec."""
    only_admin(account)
    await limited(request, ratelimit.WEEKLY_QUEST_BY_ACCOUNT, str(account.id))
    try:
        template = await weekly.promote(db, quest_id, body.key, body.name, body.min_level, body.max_level, body.weight, body.spec)
    except weekly.WeeklyError as error:
        raise refuse(error) from error
    return {"template": {"id": template.id, "key": template.key, "name": template.name, "min_level": template.min_level, "max_level": template.max_level,
                         "weight": template.weight, "spec": template.spec}}
