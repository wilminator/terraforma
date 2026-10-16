"""The manual quest calls: an admin writes an event (an alliance competition, a coliseum tournament), publishes it, judges its entries and
cancels it; a player's team looks at the open events, enters one and reads the standings.

Each call is its own route with a strict model. Calls that change something need a login and the CSRF token (``ActingAccount``) and are
rate-limited per account; calls that only read need a login (``CurrentAccount``). Finishing an entry is the game's (its dungeon calls
``quests.manual.finish``) or, for a contest an admin judges, the admin's ``result`` call.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request, status
from pydantic import Field
from sqlalchemy import select

from ..accounts import ratelimit
from ..accounts.routes import Strict, limited
from ..api.deps import ActingAccount, CurrentAccount, Db, GameManualQuests
from ..heroes import service as heroes
from ..heroes.models import Team
from . import manual
from .manual_models import CANCELLED, DRAFT, PUBLISHED

router = APIRouter(prefix="/api")

TeamId = Annotated[int, Path(ge=1)]
EventId = Annotated[int, Path(ge=1)]
#: A moment in time, in seconds since 1970: up to the year 2100.
Seconds = Annotated[int, Field(ge=0, le=4_102_444_800)]


class NewEvent(Strict):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="", max_length=32)
    description: str = Field(default="", max_length=manual.MAX_DESCRIPTION)
    spec: dict = Field(default_factory=dict)
    opens_at: Seconds
    closes_at: Seconds
    ends_at: Seconds
    limit_seconds: int = Field(default=0, ge=0, le=manual.MAX_SPAN)
    max_entries: int = Field(default=0, ge=0, le=1_000_000)
    map_id: int | None = Field(default=None, ge=1)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)


class ChangeEvent(Strict):
    """The fields to rewrite on a draft; what is left out stays as it was."""

    key: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=64)
    kind: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=manual.MAX_DESCRIPTION)
    spec: dict | None = None
    opens_at: Seconds | None = None
    closes_at: Seconds | None = None
    ends_at: Seconds | None = None
    limit_seconds: int | None = Field(default=None, ge=0, le=manual.MAX_SPAN)
    max_entries: int | None = Field(default=None, ge=0, le=1_000_000)
    map_id: int | None = Field(default=None, ge=1)
    x: int | None = Field(default=None, ge=0)
    y: int | None = Field(default=None, ge=0)


class Result(Strict):
    won: bool
    score: int | None = Field(default=None, ge=0, le=manual.MAX_SCORE)


def refuse(error: Exception) -> HTTPException:
    code = status.HTTP_404_NOT_FOUND if isinstance(error, heroes.NotFound) or "no such" in str(error) else status.HTTP_422_UNPROCESSABLE_CONTENT
    return HTTPException(code, str(error))


def only_admin(account) -> None:
    if not account.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "only an admin may write events")


async def count(db, quest) -> int:
    return len(await manual.entries(db, quest))


# --- the admin's portal ------------------------------------------------------------------------------------------------

@router.get("/admin/manual-quests")
async def admin_listing(account: CurrentAccount, db: Db, event_status: Annotated[str | None, Query(alias="status", pattern=f"^({DRAFT}|{PUBLISHED}|{CANCELLED})$")] = None,
                        after: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> dict:
    """The events, oldest first after the id $after; ``status`` narrows to one stage."""
    only_admin(account)
    return {"events": [manual.view(each, entered=await count(db, each)) for each in await manual.listing(db, event_status, after, limit)]}


@router.get("/admin/manual-quests/{event_id}")
async def admin_event(event_id: EventId, account: CurrentAccount, db: Db) -> dict:
    """One event with every entry, best score first."""
    only_admin(account)
    try:
        quest = await manual.get(db, event_id)
    except manual.ManualError as error:
        raise refuse(error) from error
    ranked = await manual.entries(db, quest)
    names = {team.id: team.name for team in await _teams(db, [each.team_id for each in ranked])}
    return {"event": manual.view(quest, entered=len(ranked)), "entries": [{**manual.entry_view(each), "team": names.get(each.team_id)} for each in ranked]}


async def _teams(db, ids: list[int]) -> list[Team]:
    return list((await db.scalars(select(Team).where(Team.id.in_(ids)))).all()) if ids else []


@router.post("/admin/manual-quests", status_code=status.HTTP_201_CREATED)
async def create(body: NewEvent, request: Request, account: ActingAccount, db: Db) -> dict:
    """Writes a new event as a draft: teams don't see it until it is published."""
    only_admin(account)
    await limited(request, ratelimit.MANUAL_QUEST_BY_ACCOUNT, str(account.id))
    try:
        return {"event": manual.view(await manual.create(db, account.id, **body.model_dump()), entered=0)}
    except manual.ManualError as error:
        raise refuse(error) from error


@router.patch("/admin/manual-quests/{event_id}")
async def change(event_id: EventId, body: ChangeEvent, request: Request, account: ActingAccount, db: Db) -> dict:
    """Rewrites the fields given on a draft."""
    only_admin(account)
    await limited(request, ratelimit.MANUAL_QUEST_BY_ACCOUNT, str(account.id))
    fields = body.model_dump(exclude_unset=True)
    for name in ("key", "name", "kind", "description", "spec", "opens_at", "closes_at", "ends_at", "limit_seconds", "max_entries"):
        if name in fields and fields[name] is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{name} can't be null")
    try:
        return {"event": manual.view(await manual.update(db, event_id, **fields), entered=0)}
    except manual.ManualError as error:
        raise refuse(error) from error


async def stage(action, event_id: int, request: Request, account, db) -> dict:
    only_admin(account)
    await limited(request, ratelimit.MANUAL_QUEST_BY_ACCOUNT, str(account.id))
    try:
        quest = await action(db, event_id)
        return {"event": manual.view(quest, entered=await count(db, quest))}
    except manual.ManualError as error:
        raise refuse(error) from error


@router.post("/admin/manual-quests/{event_id}/publish")
async def publish(event_id: EventId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Shows a draft to the teams (they enter from its opening time)."""
    return await stage(manual.publish, event_id, request, account, db)


@router.post("/admin/manual-quests/{event_id}/cancel")
async def cancel(event_id: EventId, request: Request, account: ActingAccount, db: Db) -> dict:
    """Calls an event off: the teams still in it are done with it and nobody is penalized."""
    return await stage(manual.cancel, event_id, request, account, db)


@router.post("/admin/manual-quests/{event_id}/entries/{entry_id}/result")
async def result(event_id: EventId, entry_id: Annotated[int, Path(ge=1)], body: Result, request: Request, account: ActingAccount, db: Db, hooks: GameManualQuests) -> dict:
    """Judges an entry that is still running (an admin's contest): won or lost, with a score if given."""
    only_admin(account)
    await limited(request, ratelimit.MANUAL_QUEST_BY_ACCOUNT, str(account.id))
    try:
        entry = await manual.get_entry(db, entry_id)
        if entry.quest_id != event_id:
            raise manual.ManualError("there's no such entry")
        return {"entry": manual.entry_view(await manual.finish(db, hooks, entry_id, body.won, body.score))}
    except manual.ManualError as error:
        raise refuse(error) from error


# --- the players' calls ------------------------------------------------------------------------------------------------

@router.get("/teams/{team_id}/manual-quests")
async def events(team_id: TeamId, account: CurrentAccount, db: Db, hooks: GameManualQuests) -> dict:
    """The published events that are not over, each with the team's entry if it has one."""
    try:
        await heroes.own_team(db, account, team_id)
    except heroes.NotFound as error:
        raise refuse(error) from error
    await manual.settle(db, hooks, team_id)
    return {"events": [manual.view(each, await manual.entry_of(db, team_id, each.id)) for each in await manual.open_now(db)]}


@router.post("/teams/{team_id}/manual-quests/{event_id}/enter")
async def enter(team_id: TeamId, event_id: EventId, request: Request, account: ActingAccount, db: Db, hooks: GameManualQuests) -> dict:
    """The team enters an open event and its clock starts (the same entry if it already entered)."""
    await limited(request, ratelimit.MANUAL_QUEST_BY_ACCOUNT, str(account.id))
    try:
        await heroes.own_team(db, account, team_id)
        entry = await manual.enter(db, hooks, team_id, event_id)
        return {"event": manual.view(await manual.get(db, event_id), entry)}
    except (heroes.NotFound, manual.ManualError) as error:
        raise refuse(error) from error


@router.get("/manual-quests/{event_id}/standings")
async def standings(event_id: EventId, account: CurrentAccount, db: Db) -> dict:
    """A published event's entries, best score first, by team name only."""
    try:
        quest = await manual.get(db, event_id)
        if quest.status == DRAFT:
            raise manual.ManualError("there's no such event")
    except manual.ManualError as error:
        raise refuse(error) from error
    ranked = await manual.entries(db, quest)
    names = {team.id: team.name for team in await _teams(db, [each.team_id for each in ranked])}
    return {"event": quest.name, "standings": [{"rank": place, "team": names.get(each.team_id), "score": each.score, "status": each.status}
                                               for place, each in enumerate(ranked, 1)]}
