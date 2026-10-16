"""Manual quests: events an admin writes by hand and teams enter while they are open.

An event is a draft until an admin publishes it; then teams see it and enter from ``opens_at`` until ``closes_at``. Entering starts the team's
clock: it has ``limit_seconds`` (0: until the event is over) and never longer than ``ends_at``. The game's dungeon (or an admin, for a judged
contest) finishes the entry with a score; a won entry is added to the team's quest record (category ``event``, level 0, and the event's own
key as a category of its own, so a dialog can ask either). Nothing runs on a timer: an entry past its deadline fails with ``timeout`` the next
time anything touches the team or the event, so any server of a cluster gives the same answer. Cancelling an event ends its active entries
with no penalty.
"""

import json
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..heroes.models import Team
from ..models import Map
from . import service
from .hooks import ManualQuests
from .manual_models import (
    ACTIVE, CANCELLED, CANCELLED_BY_ADMIN, COMPLETED, DRAFT, ENDED, FAILED, POOR_PERFORMANCE, PUBLISHED, TIMEOUT, ManualEntry, ManualQuest,
)
from .weekly import MAX_SPEC

#: The category a won event is added to in the team's quest record (``quests.complete``), at level 0; the event's key is a category too.
CATEGORY = "event"
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MAX_DESCRIPTION = 4000
MAX_SCORE = 1_000_000_000
#: The most an event may be open or run: a typing slip (milliseconds for seconds) should be refused, not accepted.
MAX_SPAN = 5 * 366 * 24 * 60 * 60


class ManualError(service.QuestError):
    """Something the caller can fix: the message says what."""


async def _check(session: AsyncSession, fields: dict) -> dict:
    """Checks (and cleans) the fields an admin gave for an event; ManualError says what is wrong."""
    fields = dict(fields)
    if "key" in fields and not KEY.match(fields["key"]):
        raise ManualError("an event's key is lowercase letters, digits, - and _, up to 64 characters")
    if "name" in fields:
        fields["name"] = _name(fields["name"])
    if "spec" in fields:
        fields["spec"] = _spec(fields["spec"])
    if len(fields.get("description", "")) > MAX_DESCRIPTION:
        raise ManualError(f"an event's description is at most {MAX_DESCRIPTION} characters")
    if len(fields.get("kind", "")) > 32:
        raise ManualError("an event's kind is at most 32 characters")
    for name in ("limit_seconds", "max_entries"):
        if fields.get(name, 0) < 0:
            raise ManualError(f"{name} is not below 0")
    return fields


def _name(name: str) -> str:
    name = " ".join(name.split())
    if not 1 <= len(name) <= 64:
        raise ManualError("an event's name is 1 to 64 characters")
    return name


def _spec(spec: object) -> dict:
    if not isinstance(spec, dict):
        raise ManualError("an event's spec is a JSON object")
    if len(json.dumps(spec)) > MAX_SPEC:
        raise ManualError(f"an event's spec is at most {MAX_SPEC} characters of JSON")
    return spec


def _check_times(opens_at: int, closes_at: int, ends_at: int, limit_seconds: int) -> None:
    if not 0 <= opens_at < closes_at <= ends_at:
        raise ManualError("an event opens before it closes, and closes no later than it ends")
    if ends_at - opens_at > MAX_SPAN or limit_seconds > MAX_SPAN:
        raise ManualError("an event runs for five years at most")


async def _check_place(session: AsyncSession, map_id: int | None, x: int | None, y: int | None) -> None:
    if map_id is None:
        if x is not None or y is not None:
            raise ManualError("a place needs its map")
        return
    found = await session.get(Map, map_id)
    if found is None:
        raise ManualError("there's no such map")
    if x is None or y is None or not (0 <= x < found.width and 0 <= y < found.height):
        raise ManualError("the event's place is a tile of its map")


async def create(session: AsyncSession, author_id: int | None, **fields) -> ManualQuest:
    """A new draft event. $fields: key, name, kind, description, spec, opens_at, closes_at, ends_at, limit_seconds, max_entries, map_id, x, y."""
    fields = await _check(session, fields)
    _check_times(fields["opens_at"], fields["closes_at"], fields["ends_at"], fields.get("limit_seconds", 0))
    await _check_place(session, fields.get("map_id"), fields.get("x"), fields.get("y"))
    if await session.scalar(select(ManualQuest.id).where(ManualQuest.key == fields["key"])) is not None:
        raise ManualError("an event with this key already exists")
    quest = ManualQuest(status=DRAFT, author_id=author_id, **fields)
    try:
        async with session.begin_nested():
            session.add(quest)
            await session.flush()
    except IntegrityError as error:  # (two admins used the key at once)
        raise ManualError("an event with this key already exists") from error
    return quest


async def get(session: AsyncSession, quest_id: int) -> ManualQuest:
    quest = await session.get(ManualQuest, quest_id)
    if quest is None:
        raise ManualError("there's no such event")
    return quest


async def update(session: AsyncSession, quest_id: int, **fields) -> ManualQuest:
    """Rewrites a draft (only the fields given). A published event is not rewritten under the teams that saw it: cancel it and write another."""
    quest = await get(session, quest_id)
    if quest.status != DRAFT:
        raise ManualError(f"only a draft can be changed, and this event is {quest.status}")
    fields = await _check(session, fields)
    if "key" in fields and fields["key"] != quest.key and await session.scalar(select(ManualQuest.id).where(ManualQuest.key == fields["key"])) is not None:
        raise ManualError("an event with this key already exists")
    merged = {name: fields.get(name, getattr(quest, name)) for name in ("opens_at", "closes_at", "ends_at", "limit_seconds", "map_id", "x", "y")}
    _check_times(merged["opens_at"], merged["closes_at"], merged["ends_at"], merged["limit_seconds"])
    await _check_place(session, merged["map_id"], merged["x"], merged["y"])
    for name, value in fields.items():
        setattr(quest, name, value)
    await session.flush()
    return quest


async def publish(session: AsyncSession, quest_id: int) -> ManualQuest:
    """Shows a draft to the teams. An event whose time to close has already passed can't be published."""
    quest = await get(session, quest_id)
    if quest.status != DRAFT:
        raise ManualError(f"only a draft can be published, and this event is {quest.status}")
    if wallclock.timestamp() >= quest.closes_at:
        raise ManualError("this event has already closed: change its times first")
    quest.status = PUBLISHED
    await session.flush()
    return quest


async def cancel(session: AsyncSession, quest_id: int) -> ManualQuest:
    """Calls a published event off (or throws a draft away): the teams still in it are done with it, with no penalty."""
    quest = await get(session, quest_id)
    if quest.status == CANCELLED:
        raise ManualError("this event is already cancelled")
    was = quest.status
    quest.status = CANCELLED
    now = wallclock.timestamp()
    if was == PUBLISHED:
        for entry in (await session.scalars(select(ManualEntry).where(ManualEntry.quest_id == quest.id, ManualEntry.status == ACTIVE))).all():
            entry.status, entry.failure, entry.finished_at = ENDED, CANCELLED_BY_ADMIN, now
    await session.flush()
    return quest


async def listing(session: AsyncSession, status: str | None = None, after: int = 0, limit: int = 50) -> list[ManualQuest]:
    query = select(ManualQuest).where(ManualQuest.id > after)
    if status is not None:
        query = query.where(ManualQuest.status == status)
    return list((await session.scalars(query.order_by(ManualQuest.id).limit(limit))).all())


async def open_now(session: AsyncSession) -> list[ManualQuest]:
    """The published events that are not over yet (the ones a player's list shows)."""
    return list((await session.scalars(select(ManualQuest).where(ManualQuest.status == PUBLISHED, ManualQuest.ends_at > wallclock.timestamp()).order_by(ManualQuest.id))).all())


async def entry_of(session: AsyncSession, team_id: int, quest_id: int) -> ManualEntry | None:
    return await session.scalar(select(ManualEntry).where(ManualEntry.team_id == team_id, ManualEntry.quest_id == quest_id))


async def entries(session: AsyncSession, quest: ManualQuest) -> list[ManualEntry]:
    """The event's entries, best score first (earliest to finish breaks a tie, then who entered first). Settles the overdue ones first."""
    await settle_quest(session, None, quest)
    ranked = await session.scalars(select(ManualEntry).where(ManualEntry.quest_id == quest.id).order_by(
        ManualEntry.score.desc(), func.coalesce(ManualEntry.finished_at, ManualEntry.deadline), ManualEntry.id))
    return list(ranked.all())


async def settle_quest(session: AsyncSession, hooks: ManualQuests | None, quest: ManualQuest) -> None:
    """Fails (``timeout``) the quest's active entries that ran past their deadline."""
    now = wallclock.timestamp()
    late = await session.scalars(select(ManualEntry).where(ManualEntry.quest_id == quest.id, ManualEntry.status == ACTIVE, ManualEntry.deadline < now))
    for entry in late.all():
        await _fail(session, hooks, entry, quest, TIMEOUT, now)
    await session.flush()


async def settle(session: AsyncSession, hooks: ManualQuests | None, team_id: int) -> None:
    """Fails (``timeout``) the team's active entries that ran past their deadline. Called by everything that touches a team's events."""
    now = wallclock.timestamp()
    late = await session.execute(select(ManualEntry, ManualQuest).join(ManualQuest, ManualQuest.id == ManualEntry.quest_id).where(
        ManualEntry.team_id == team_id, ManualEntry.status == ACTIVE, ManualEntry.deadline < now))
    for entry, quest in late.all():
        await _fail(session, hooks, entry, quest, TIMEOUT, now)
    await session.flush()


async def _fail(session: AsyncSession, hooks: ManualQuests | None, entry: ManualEntry, quest: ManualQuest, reason: str, now: int) -> None:
    entry.status, entry.failure, entry.finished_at = FAILED, reason, now
    await session.flush()
    await (hooks or ManualQuests()).penalty(session, entry.team_id, quest, reason)


async def enter(session: AsyncSession, hooks: ManualQuests, team_id: int, quest_id: int) -> ManualEntry:
    """The team enters an event that is open: its clock starts now. The team's own entry is returned if it already entered."""
    if await session.get(Team, team_id) is None:
        raise ManualError("there's no such team")
    quest = await get(session, quest_id)
    if quest.status != PUBLISHED:
        raise ManualError("there's no such event")  # (a draft or a cancelled event does not exist for a team)
    await settle(session, hooks, team_id)
    existing = await entry_of(session, team_id, quest.id)
    if existing is not None:
        return existing
    now = wallclock.timestamp()
    if now < quest.opens_at:
        raise ManualError("this event has not opened yet")
    if now >= quest.closes_at:
        raise ManualError("this event is closed to new entries")
    if quest.max_entries and await session.scalar(select(func.count()).select_from(ManualEntry).where(ManualEntry.quest_id == quest.id)) >= quest.max_entries:
        raise ManualError("this event is full")
    why = await hooks.may_enter(session, team_id, quest)
    if why is not None:
        raise ManualError(why)
    deadline = min(now + quest.limit_seconds, quest.ends_at) if quest.limit_seconds else quest.ends_at
    row = ManualEntry(quest_id=quest.id, team_id=team_id, status=ACTIVE, started_at=now, deadline=deadline)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # (the team entered twice at once)
        return await entry_of(session, team_id, quest.id)
    return row


async def get_entry(session: AsyncSession, entry_id: int) -> ManualEntry:
    entry = await session.get(ManualEntry, entry_id)
    if entry is None:
        raise ManualError("there's no such entry")
    return entry


async def finish(session: AsyncSession, hooks: ManualQuests | None, entry_id: int, won: bool, score: int | None = None, reason: str = POOR_PERFORMANCE) -> ManualEntry:
    """An active entry ends, with a $score if given (it is kept as it is otherwise). Won in time, it is added to the team's quest record
    (categories ``event`` and the event's key, level 0); lost, the game's penalty applies for $reason. An entry past its deadline has already
    failed with ``timeout``, whatever the outcome."""
    if score is not None and not 0 <= score <= MAX_SCORE:
        raise ManualError(f"a score is a whole number from 0 to {MAX_SCORE}")
    if reason not in (TIMEOUT, POOR_PERFORMANCE):
        raise ManualError("an entry fails for running out of time or for poor performance")
    entry = await get_entry(session, entry_id)
    was = entry.status
    await settle(session, hooks, entry.team_id)
    await session.refresh(entry)
    if was == ACTIVE and entry.status == FAILED:
        return entry  # (ran out of time first)
    if entry.status != ACTIVE:
        raise ManualError(f"that entry is already {entry.status}")
    quest = await session.get(ManualQuest, entry.quest_id)
    now = wallclock.timestamp()
    if score is not None:
        entry.score = score
    if won:
        entry.status, entry.finished_at = COMPLETED, now
        await service.complete(session, entry.team_id, CATEGORY, 0)
        await service.complete(session, entry.team_id, quest.key, 0)
        await session.flush()
    else:
        await _fail(session, hooks, entry, quest, reason, now)
    return entry


async def active(session: AsyncSession, team_id: int, key: str) -> ManualEntry | None:
    """The team's entry in the event with this key if it is still running (settle first to be sure it is still in time)."""
    return await session.scalar(select(ManualEntry).join(ManualQuest, ManualQuest.id == ManualEntry.quest_id).where(
        ManualEntry.team_id == team_id, ManualEntry.status == ACTIVE, ManualQuest.key == key).limit(1))


async def forget_team(session: AsyncSession, team_id: int) -> None:
    """A team that is deleted takes its entries with it."""
    for each in (await session.scalars(select(ManualEntry).where(ManualEntry.team_id == team_id))).all():
        await session.delete(each)
    await session.flush()


def view(quest: ManualQuest, entry: ManualEntry | None = None, entered: int | None = None) -> dict:
    """What a player sees of an event, and of their team's entry. $entered counts the entries (an admin's view)."""
    shown = {"id": quest.id, "key": quest.key, "kind": quest.kind, "name": quest.name, "description": quest.description, "spec": quest.spec,
             "status": quest.status, "opens": quest.opens_at, "closes": quest.closes_at, "ends": quest.ends_at, "limit_seconds": quest.limit_seconds,
             "max_entries": quest.max_entries, "map_id": quest.map_id, "x": quest.x, "y": quest.y}
    if entered is not None:
        shown["entered"] = entered
    if entry is not None:
        shown["team"] = entry_view(entry)
    return shown


def entry_view(entry: ManualEntry) -> dict:
    return {"id": entry.id, "team_id": entry.team_id, "status": entry.status, "score": entry.score, "started": entry.started_at,
            "deadline": entry.deadline, "finished": entry.finished_at, "failure": entry.failure}
