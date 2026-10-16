"""The weekly quest: each team is offered one a week, from a template or generated, and every generated one is kept.

``offer`` gives a team its quest for this week (the same one if asked again); the game starts and finishes it with ``start`` and
``finish`` (a quest not started when the week ends simply lapses, and a team that fails one has lost it: there is no second try).
A quest taken has a wall-clock limit, the game's ``WeeklyQuests.limit_seconds``: no clock runs anywhere, so a team's overdue quest fails
with ``timeout`` the next time anything touches the team (``settle``). A team that fails by timeout or by poor performance costs whatever
the game's ``WeeklyQuests.penalty`` says (nothing by default).
``promote`` makes a generated quest a permanent template, with or without tweaks. The quest's contents are the game's (``WeeklyQuests``)."""

import json
import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..db.dialect import upsert
from ..heroes.models import Team
from ..models import World
from ..world.rng import WorldRng
from ..world.start import ensure_start
from . import service
from .hooks import WeeklyQuests
from .rotation_models import POOR_PERFORMANCE, TIMEOUT
from .weekly_models import ACTIVE, COMPLETED, FAILED, FROM_TEMPLATE, GENERATED, OFFERED, PROMOTED, SEEDED, QuestTemplate, WeeklyQuest

WEEK_SECONDS = 7 * 24 * 60 * 60
#: 1970-01-01 was a Thursday: weeks begin on a Monday 00:00 UTC, four days later.
_FIRST_MONDAY = 4 * 24 * 60 * 60
#: The category a completed weekly quest is added to in the team's quest record (``quests.complete``), at the quest's level.
CATEGORY = "weekly"
MAX_SPEC = 16_000
MAX_LEVEL = 999
KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class WeeklyError(service.QuestError):
    """Something the caller can fix: the message says what."""


def week_of(timestamp: int) -> int:
    return (timestamp - _FIRST_MONDAY) // WEEK_SECONDS


def week_ends(week: int) -> datetime:
    """When the week is over (the next Monday 00:00 UTC), and with it the chance to take that week's quest."""
    return datetime.fromtimestamp(_FIRST_MONDAY + (week + 1) * WEEK_SECONDS, tz=UTC)


def check_spec(spec: object) -> dict:
    if not isinstance(spec, dict):
        raise WeeklyError("a quest's spec is a JSON object")
    if len(json.dumps(spec)) > MAX_SPEC:
        raise WeeklyError(f"a quest's spec is at most {MAX_SPEC} characters of JSON")
    return spec


def check_name(name: str) -> str:
    name = " ".join(name.split())
    if not 1 <= len(name) <= 64:
        raise WeeklyError("a quest's name is 1 to 64 characters")
    return name


async def apply_templates(session: AsyncSession, rows: list) -> int:
    """Makes the seed's templates match the file: new keys added, known keys rewritten, keys the file no longer lists made inactive.
    Promoted templates belong to the database and are left alone (a promoted template cannot share a seeded key)."""
    for row in rows:
        existing = await session.scalar(select(QuestTemplate).where(QuestTemplate.key == row.key))
        if existing is not None and existing.source == PROMOTED:
            raise WeeklyError(f"quest_templates.json {row.key}: an admin already promoted a quest to a template with this key")
        await upsert(session, QuestTemplate.__table__, {
            "key": row.key, "name": row.name, "source": SEEDED, "min_level": row.min_level, "max_level": row.max_level,
            "weight": row.weight, "spec": row.spec, "slot": row.slot, "pxp_min": row.pxp_min, "pxp_max": row.pxp_max, "active": True}, ["key"])
    gone = await session.scalars(select(QuestTemplate).where(QuestTemplate.source == SEEDED, QuestTemplate.key.not_in([row.key for row in rows])))
    for template in gone.all():
        template.active = False
    await session.flush()
    return len(rows)


async def current(session: AsyncSession, team_id: int, week: int | None = None) -> WeeklyQuest | None:
    """The team's quest for the week (this one by default), if it has been offered one."""
    week = week_of(wallclock.timestamp()) if week is None else week
    return await session.scalar(select(WeeklyQuest).where(WeeklyQuest.team_id == team_id, WeeklyQuest.week == week))


async def settle(session: AsyncSession, hooks: WeeklyQuests, team_id: int) -> None:
    """Fails the team's started quest if its time ran out: ``timeout``, and the game's penalty applies. Called by everything that touches a
    team's weekly quests."""
    quest = await _started(session, team_id)
    if quest is not None and wallclock.timestamp() > (quest.deadline or 0):
        await _fail(session, hooks, quest, TIMEOUT)


async def _fail(session: AsyncSession, hooks: WeeklyQuests, quest: WeeklyQuest, reason: str) -> None:
    quest.status, quest.failure, quest.finished_at = FAILED, reason, wallclock.now()
    await session.flush()
    if quest.team_id is not None:
        await hooks.penalty(session, quest.team_id, quest, reason)


async def active(session: AsyncSession, hooks: WeeklyQuests, team_id: int) -> WeeklyQuest | None:
    """The team's started, unfinished quest in time: the latest, which may be last week's (a quest started in time may finish late)."""
    await settle(session, hooks, team_id)
    return await _started(session, team_id)


async def _started(session: AsyncSession, team_id: int) -> WeeklyQuest | None:
    return await session.scalar(select(WeeklyQuest).where(WeeklyQuest.team_id == team_id, WeeklyQuest.status == ACTIVE).order_by(WeeklyQuest.week.desc()).limit(1))


async def offer(session: AsyncSession, hooks: WeeklyQuests, team_id: int) -> WeeklyQuest | None:
    """The team's quest for this week: the one it already has, or one drawn now. A level-fitting template is drawn by weight (``hooks.scale``
    makes it the team's), otherwise the game's ``generate`` makes one up and it is recorded as generated. None when the game makes no quest
    for this team this week (nothing fits and ``generate`` returns None)."""
    if await session.get(Team, team_id) is None:
        raise WeeklyError("there's no such team")
    week = week_of(wallclock.timestamp())
    await settle(session, hooks, team_id)
    existing = await current(session, team_id, week)
    if existing is not None:
        return existing
    level = min(MAX_LEVEL, max(1, await hooks.level(session, team_id) + await hooks.difficulty(session, team_id)))
    world = await session.get(World, (await ensure_start(session)).world_id)
    rng = WorldRng(world.seed).stream("weekly-quest", team_id, week)
    fitting = list((await session.scalars(select(QuestTemplate).where(
        QuestTemplate.active.is_(True), QuestTemplate.slot.is_(None), QuestTemplate.min_level <= level, QuestTemplate.max_level >= level).order_by(QuestTemplate.id))).all())
    if fitting and hooks.use_template(rng, len(fitting)):
        template = rng.choices(fitting, weights=[max(1, entry.weight) for entry in fitting])[0]
        quest = WeeklyQuest(team_id=team_id, week=week, source=FROM_TEMPLATE, template_id=template.id, name=template.name, level=level,
                            spec=check_spec(hooks.scale(template, level)))
    else:
        made = hooks.generate(rng, team_id, level)
        if made is None:
            return None
        quest = WeeklyQuest(team_id=team_id, week=week, source=GENERATED, name=check_name(made.name), level=level, spec=check_spec(made.spec))
    try:
        async with session.begin_nested():
            session.add(quest)
            await session.flush()
    except IntegrityError:  # (the team asked twice at once: the first draw stands)
        return await current(session, team_id, week)
    return quest


async def get(session: AsyncSession, quest_id: int) -> WeeklyQuest:
    quest = await session.get(WeeklyQuest, quest_id)
    if quest is None:
        raise WeeklyError("there's no such weekly quest")
    return quest


async def start(session: AsyncSession, hooks: WeeklyQuests, quest_id: int) -> WeeklyQuest:
    """The team takes the quest (this week's only): from now on it can be finished, and not walked away from. The clock runs: the team has
    ``hooks.limit_seconds`` for it."""
    quest = await get(session, quest_id)
    if quest.status != OFFERED:
        raise WeeklyError(f"that quest is already {quest.status}")
    if quest.week != week_of(wallclock.timestamp()):
        raise WeeklyError("that quest was last week's and has lapsed")
    limit = hooks.limit_seconds(quest.level)
    if limit < 1:
        raise WeeklyError("a quest's time limit is at least a second")
    if quest.team_id is not None:
        await settle(session, hooks, quest.team_id)
        if await _started(session, quest.team_id) is not None:
            raise WeeklyError("the team is already on a weekly quest")
    quest.status, quest.started_at = ACTIVE, wallclock.timestamp()
    quest.deadline = quest.started_at + limit
    await session.flush()
    return quest


async def finish(session: AsyncSession, hooks: WeeklyQuests, quest_id: int, won: bool, reason: str = POOR_PERFORMANCE) -> WeeklyQuest:
    """A started quest ends. Won in time, it is added to the team's quest record (category ``weekly``, at its level); lost, it is lost for the
    week and the game's penalty applies for $reason (``poor_performance`` unless the game says otherwise). A quest started in time may finish
    after the week turns, but not after its own deadline: it has already failed with ``timeout``, whatever the outcome."""
    quest = await get(session, quest_id)
    was = quest.status
    if was == ACTIVE and quest.team_id is not None:
        await settle(session, hooks, quest.team_id)
    if was == ACTIVE and quest.status == FAILED:
        return quest  # (ran out of time first)
    if quest.status != ACTIVE:
        raise WeeklyError("only a quest that was started can be finished" if quest.status == OFFERED else f"that quest is already {quest.status}")
    if reason not in (TIMEOUT, POOR_PERFORMANCE):
        raise WeeklyError("a quest fails for running out of time or for poor performance")
    if won:
        quest.status, quest.finished_at = COMPLETED, wallclock.now()
        if quest.team_id is not None:
            await service.complete(session, quest.team_id, CATEGORY, quest.level)
        await session.flush()
    else:
        await _fail(session, hooks, quest, reason)
    return quest


async def generated(session: AsyncSession, promoted: bool | None = None, after: int = 0, limit: int = 50) -> list[WeeklyQuest]:
    """The recorded generated quests, oldest first after id $after; $promoted narrows to those made a template (True) or not yet (False)."""
    query = select(WeeklyQuest).where(WeeklyQuest.source == GENERATED, WeeklyQuest.id > after)
    if promoted is not None:
        query = query.where(WeeklyQuest.promoted_template_id.is_not(None) if promoted else WeeklyQuest.promoted_template_id.is_(None))
    return list((await session.scalars(query.order_by(WeeklyQuest.id).limit(limit))).all())


async def promote(session: AsyncSession, quest_id: int, key: str, name: str | None = None, min_level: int | None = None,
                  max_level: int | None = None, weight: int = 1, spec: dict | None = None) -> QuestTemplate:
    """Makes a generated quest a permanent template under $key. Without tweaks it keeps the quest's name and spec and fits teams at exactly
    its level; $name, the level range, $weight and $spec change what the template gets. Each generated quest can be promoted once."""
    quest = await get(session, quest_id)
    if quest.source != GENERATED:
        raise WeeklyError("only a generated quest can be promoted: that one already came from a template")
    if quest.promoted_template_id is not None:
        raise WeeklyError("that quest was already promoted to a template")
    if not KEY.fullmatch(key):
        raise WeeklyError("a key is 1-64 lowercase letters, digits, _ or -, starting with a letter or digit")
    low, high = (quest.level if min_level is None else min_level), (quest.level if max_level is None else max_level)
    if not 1 <= low <= high <= MAX_LEVEL:
        raise WeeklyError(f"a template's levels run from 1 to {MAX_LEVEL}, lowest first")
    if weight < 1:
        raise WeeklyError("a template's weight is at least 1")
    template = QuestTemplate(key=key, name=check_name(name if name is not None else quest.name), source=PROMOTED, min_level=low, max_level=high,
                             weight=weight, spec=check_spec(quest.spec if spec is None else spec))
    try:
        async with session.begin_nested():
            session.add(template)
            await session.flush()
    except IntegrityError as error:
        raise WeeklyError(f"there is already a template with the key {key!r}") from error
    quest.promoted_template_id = template.id
    await session.flush()
    return template


async def forget_team(session: AsyncSession, team_id: int) -> None:
    """A team that is deleted takes its weekly quests with it, except the generated ones, which stay (owned by nobody) to be promoted."""
    for quest in (await session.scalars(select(WeeklyQuest).where(WeeklyQuest.team_id == team_id))).all():
        if quest.source == GENERATED:
            quest.team_id = None
        else:
            await session.delete(quest)
    await session.flush()


def view(quest: WeeklyQuest) -> dict:
    """What a player sees of their quest."""
    return {"id": quest.id, "week": quest.week, "ends": week_ends(quest.week).isoformat(), "name": quest.name, "level": quest.level,
            "status": quest.status, "spec": quest.spec, "started": quest.started_at, "deadline": quest.deadline, "failure": quest.failure}


def admin_view(quest: WeeklyQuest) -> dict:
    return {**view(quest), "team_id": quest.team_id, "source": quest.source, "template_id": quest.template_id, "promoted_template_id": quest.promoted_template_id}
