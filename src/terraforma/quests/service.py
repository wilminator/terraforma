"""Quest records and markers. A game awards a quest with ``complete``; the dialog tags ``quests``, ``quest_marker`` and
``set_quest_marker`` read and write through here."""

import operator

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .models import QuestMarker, QuestRecord

#: How a count or marker is compared with a number, as the dialog tags write it: equal, not equal, less than, less or equal,
#: greater than, greater or equal.
OPERATORS = {"eq": operator.eq, "ne": operator.ne, "lt": operator.lt, "le": operator.le, "gt": operator.gt, "ge": operator.ge}

MAX_VALUE = 1_000_000


class QuestError(ValueError):
    """Something the caller can fix: the message says what."""


def compare(op: str, left: int, right: int) -> bool:
    if op not in OPERATORS:
        raise QuestError(f"there is no comparison {op!r}: use one of {', '.join(OPERATORS)}")
    return OPERATORS[op](left, right)


async def complete(session: AsyncSession, team_id: int, category: str, level: int, times: int = 1) -> int:
    """The team completed $times quests of a category and level: added to its permanent record. Returns the new count."""
    if times < 1 or not category or level < 0:
        raise QuestError("a quest has a category, a level of 0 or more, and is completed at least once")
    for _ in range(2):  # (two completions at the same instant can both find no row: the loser adds to the winner's)
        row = await session.scalar(select(QuestRecord).where(QuestRecord.team_id == team_id, QuestRecord.category == category, QuestRecord.level == level))
        if row is not None:
            row.count += times
            await session.flush()
            return row.count
        try:
            async with session.begin_nested():
                session.add(QuestRecord(team_id=team_id, category=category, level=level, count=times))
                await session.flush()
            return times
        except IntegrityError:
            continue
    raise QuestError("could not record that quest, try again")


async def completed(session: AsyncSession, team_id: int, category: str, level: int | None = None) -> int:
    """How many quests of the category the team completed, at the level or (None) at any level."""
    query = select(func.coalesce(func.sum(QuestRecord.count), 0)).where(QuestRecord.team_id == team_id, QuestRecord.category == category)
    if level is not None:
        query = query.where(QuestRecord.level == level)
    return int(await session.scalar(query))


async def marker(session: AsyncSession, team_id: int, quest: str) -> int:
    """The team's marker for the quest (0 when it has none)."""
    value = await session.scalar(select(QuestMarker.value).where(QuestMarker.team_id == team_id, QuestMarker.quest == quest))
    return 0 if value is None else value


async def set_marker(session: AsyncSession, team_id: int, quest: str, value: int) -> None:
    """Sets the team's marker for the quest. A marker of 0 is no marker at all (its row goes)."""
    if not 0 <= value <= MAX_VALUE or not quest:
        raise QuestError(f"a marker is a whole number from 0 to {MAX_VALUE}")
    row = await session.scalar(select(QuestMarker).where(QuestMarker.team_id == team_id, QuestMarker.quest == quest))
    if value == 0:
        if row is not None:
            await session.delete(row)
    elif row is not None:
        row.value = value
    else:
        session.add(QuestMarker(team_id=team_id, quest=quest, value=value))
    await session.flush()


async def forget_team(session: AsyncSession, team_id: int) -> None:
    """A team that is deleted takes its record and markers with it."""
    await session.execute(delete(QuestRecord).where(QuestRecord.team_id == team_id))
    await session.execute(delete(QuestMarker).where(QuestMarker.team_id == team_id))
