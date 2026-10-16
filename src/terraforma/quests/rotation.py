"""Rotating quests: a slot holds one quest at a time and changes it after a random while, and teams take the quest the slot holds.

A slot is where a game puts something that changes slowly, such as a guild's rank S promotion quest. Its period lasts a whole number
of seconds drawn from the slot's range with a stream of the world's seed, so the schedule replays; the quest for a period comes from
the templates tagged for the slot (never the one it held just before, when another fits), otherwise from the game's ``generate``.
Nothing runs on a timer: a period's row is made the first time anyone asks during it, and a team's overdue quest is settled the next
time anything touches that team, so any server of a cluster gives the same answer.

A team that took a quest keeps it for the slot's time limit, and for no longer than the slot's grace time after the quest rotated out
(the area may be gone by then). The world can also end a quest at once (``end_early``): no penalty, since nobody failed. A team that fails
by running out of time, or whose game says it did badly, costs whatever the game's ``RotatingQuests.penalty`` says (nothing by default).
"""

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..db.dialect import upsert
from ..heroes.models import Team
from ..models import World
from ..world.rng import WorldRng
from ..world.start import ensure_start
from . import service
from .boss_models import QuestBossFight
from .hooks import RotatingQuests
from .rotation_models import (
    ACTIVE, COMPLETED, ENDED, FAILED, FROM_TEMPLATE, GENERATED, LAPSED, NOTHING, OFFERED, POOR_PERFORMANCE, TIMEOUT, WORLD,
    QuestSlot, SlotQuest, TeamSlotQuest,
)
from .weekly import check_name, check_spec
from .weekly_models import QuestTemplate

DAY = 24 * 60 * 60
#: The shortest period a slot may have: a slot is for things that change slowly, and every period skipped while nobody asked is counted.
MIN_PERIOD = 60 * 60


class RotationError(service.QuestError):
    """Something the caller can fix: the message says what."""


async def apply_slots(session: AsyncSession, rows: list) -> int:
    """Makes the seed's slots match the file: new keys added (their first period begins now), known keys updated (the periods already made
    keep their length), keys the file no longer lists made inactive."""
    now = wallclock.timestamp()
    for row in rows:
        if not MIN_PERIOD <= row.min_days * DAY <= row.max_days * DAY:
            raise RotationError(f"quest_slots.json {row.key}: a period runs from at least an hour up, lowest first")
        values = {"name": row.name, "guild": row.guild, "rank": row.rank, "min_seconds": row.min_days * DAY, "max_seconds": row.max_days * DAY,
                  "grace_seconds": row.grace_days * DAY, "limit_seconds": row.limit_days * DAY, "active": True}
        existing = await session.scalar(select(QuestSlot).where(QuestSlot.key == row.key))
        if existing is None:
            await upsert(session, QuestSlot.__table__, {"key": row.key, "anchor": now, **values}, ["key"])
        else:
            for name, value in values.items():
                setattr(existing, name, value)
    gone = await session.scalars(select(QuestSlot).where(QuestSlot.key.not_in([row.key for row in rows])))
    for slot in gone.all():
        slot.active = False
    await session.flush()
    return len(rows)


async def get_slot(session: AsyncSession, key: str) -> QuestSlot:
    slot = await session.scalar(select(QuestSlot).where(QuestSlot.key == key, QuestSlot.active.is_(True)))
    if slot is None:
        raise RotationError("there's no such quest slot")
    return slot


async def slots(session: AsyncSession) -> list[QuestSlot]:
    return list((await session.scalars(select(QuestSlot).where(QuestSlot.active.is_(True)).order_by(QuestSlot.id))).all())


async def _world_rng(session: AsyncSession) -> WorldRng:
    world = await session.get(World, (await ensure_start(session)).world_id)
    return WorldRng(world.seed)


def _length(rng: WorldRng, slot: QuestSlot, seq: int) -> int:
    """How long period number $seq of the slot lasts, in seconds."""
    return rng.stream("quest-slot-length", slot.key, seq).randint(max(MIN_PERIOD, slot.min_seconds), max(MIN_PERIOD, slot.max_seconds))


async def peek(session: AsyncSession, slot: QuestSlot, at: int | None = None) -> SlotQuest | None:
    """The quest the slot holds at $at (now by default) if someone has asked during the period; nothing is made."""
    at = wallclock.timestamp() if at is None else at
    return await session.scalar(select(SlotQuest).where(SlotQuest.slot_id == slot.id, SlotQuest.starts_at <= at, SlotQuest.ends_at > at))


async def current(session: AsyncSession, hooks: RotatingQuests, slot: QuestSlot) -> SlotQuest | None:
    """The quest the slot holds now: the period's row, made now if this is the first ask of the period. None when the slot's first period
    has not begun. A period in which the game made no quest has a row of source ``nothing``."""
    at = wallclock.timestamp()
    last = await session.scalar(select(SlotQuest).where(SlotQuest.slot_id == slot.id).order_by(SlotQuest.seq.desc()).limit(1))
    if last is not None and last.starts_at <= at < last.ends_at:
        return last
    seq, start = (0, slot.anchor) if last is None else (last.seq + 1, last.ends_at)
    if at < start:
        return None
    rng = await _world_rng(session)
    while True:  # (periods nobody asked about are walked over, not made)
        end = start + _length(rng, slot, seq)
        if at < end:
            break
        seq, start = seq + 1, end
    row = await _draw(session, hooks, slot, rng, seq, start, end, None if last is None else last.template_id)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # (two servers asked at once: the first draw stands, and both draws are the same)
        return await peek(session, slot, at)
    return row


async def _draw(session: AsyncSession, hooks: RotatingQuests, slot: QuestSlot, rng: WorldRng, seq: int, start: int, end: int, before: int | None) -> SlotQuest:
    fitting = list((await session.scalars(select(QuestTemplate).where(QuestTemplate.active.is_(True), QuestTemplate.slot == slot.key).order_by(QuestTemplate.id))).all())
    choices = [each for each in fitting if each.id != before] or fitting
    stream = rng.stream("quest-slot", slot.key, seq)
    if choices:
        template = stream.choices(choices, weights=[max(1, each.weight) for each in choices])[0]
        return SlotQuest(slot_id=slot.id, seq=seq, starts_at=start, ends_at=end, source=FROM_TEMPLATE, template_id=template.id, name=template.name,
                         spec=check_spec(dict(template.spec)))
    made = hooks.generate(stream, slot, seq)
    if made is None:
        return SlotQuest(slot_id=slot.id, seq=seq, starts_at=start, ends_at=end, source=NOTHING, name="", spec={})
    return SlotQuest(slot_id=slot.id, seq=seq, starts_at=start, ends_at=end, source=GENERATED, name=check_name(made.name), spec=check_spec(made.spec))


async def end_early(session: AsyncSession, hooks: RotatingQuests, slot_key: str) -> SlotQuest | None:
    """The world ended the slot's quest now (its dungeon is gone, say): teams that held it are done with it, with no penalty, and the next
    ask starts a new period with a fresh draw. Returns the quest that ended, or None if the slot held none."""
    slot = await get_slot(session, slot_key)
    quest = await current(session, hooks, slot)
    if quest is None or quest.source == NOTHING:
        return None
    now = wallclock.timestamp()
    quest.ends_at = max(quest.starts_at, now)  # (a quest ended the moment it began has a period of no length)
    quest.ended_early = True
    held = await session.scalars(select(TeamSlotQuest).where(TeamSlotQuest.slot_quest_id == quest.id, TeamSlotQuest.status.in_((OFFERED, ACTIVE))))
    for each in held.all():
        each.status, each.failure, each.finished_at = ENDED, WORLD, now
    await session.flush()
    return quest


async def settle(session: AsyncSession, hooks: RotatingQuests, team_id: int) -> None:
    """Closes the team's overdue quests: an offer the quest rotated away from lapses (no penalty), and a quest taken but not finished by its
    deadline fails with ``timeout`` (the game's penalty applies). Called by everything that touches a team's slot quests."""
    now = wallclock.timestamp()
    held = await session.execute(select(TeamSlotQuest, SlotQuest).join(SlotQuest, SlotQuest.id == TeamSlotQuest.slot_quest_id).where(
        TeamSlotQuest.team_id == team_id, TeamSlotQuest.status.in_((OFFERED, ACTIVE))).order_by(TeamSlotQuest.id))
    for progress, quest in held.all():
        if progress.status == OFFERED and now >= quest.ends_at:
            progress.status, progress.finished_at = LAPSED, now
        elif progress.status == ACTIVE and now > (progress.deadline or 0):
            await _fail(session, hooks, progress, quest, TIMEOUT, now)
    await session.flush()


async def _fail(session: AsyncSession, hooks: RotatingQuests, progress: TeamSlotQuest, quest: SlotQuest, reason: str, now: int) -> None:
    progress.status, progress.failure, progress.finished_at = FAILED, reason, now
    await session.flush()
    await hooks.penalty(session, progress.team_id, await session.get(QuestSlot, quest.slot_id), reason)


async def progress_of(session: AsyncSession, team_id: int, quest: SlotQuest) -> TeamSlotQuest | None:
    return await session.scalar(select(TeamSlotQuest).where(TeamSlotQuest.team_id == team_id, TeamSlotQuest.slot_quest_id == quest.id))


async def offer(session: AsyncSession, hooks: RotatingQuests, team_id: int, slot_key: str) -> TeamSlotQuest | None:
    """The team's hold on the slot's quest of the moment: the one it already has, or a new offer. None when the slot has no quest now (the game
    made none for this period, or the slot has not begun)."""
    if await session.get(Team, team_id) is None:
        raise RotationError("there's no such team")
    slot = await get_slot(session, slot_key)
    await settle(session, hooks, team_id)
    quest = await current(session, hooks, slot)
    if quest is None or quest.source == NOTHING:
        return None
    existing = await progress_of(session, team_id, quest)
    if existing is not None:
        return existing
    row = TeamSlotQuest(team_id=team_id, slot_quest_id=quest.id, status=OFFERED)
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError:  # (the team asked twice at once)
        return await progress_of(session, team_id, quest)
    return row


async def get(session: AsyncSession, progress_id: int) -> TeamSlotQuest:
    progress = await session.get(TeamSlotQuest, progress_id)
    if progress is None:
        raise RotationError("there's no such slot quest")
    return progress


async def start(session: AsyncSession, hooks: RotatingQuests, progress_id: int) -> TeamSlotQuest:
    """The team takes the quest it was offered, while the quest still holds the slot. From now the clock runs: the team has the slot's time limit,
    and no longer than the grace time after the quest rotates out. A team takes one quest of a slot at a time."""
    progress = await get(session, progress_id)
    await settle(session, hooks, progress.team_id)
    if progress.status != OFFERED:
        raise RotationError(f"that quest is already {progress.status}")
    quest = await session.get(SlotQuest, progress.slot_quest_id)
    slot = await session.get(QuestSlot, quest.slot_id)
    busy = await session.scalar(select(TeamSlotQuest.id).join(SlotQuest, SlotQuest.id == TeamSlotQuest.slot_quest_id).where(
        TeamSlotQuest.team_id == progress.team_id, TeamSlotQuest.status == ACTIVE, SlotQuest.slot_id == slot.id).limit(1))
    if busy is not None:
        raise RotationError("the team is already on a quest of this slot")
    now = wallclock.timestamp()
    progress.status, progress.started_at = ACTIVE, now
    progress.deadline = min(now + slot.limit_seconds, quest.ends_at + slot.grace_seconds)
    await session.flush()
    return progress


async def finish(session: AsyncSession, hooks: RotatingQuests, progress_id: int, won: bool, reason: str = POOR_PERFORMANCE) -> TeamSlotQuest:
    """A taken quest ends. Won in time, it is added to the team's quest record (category: the slot's key, level: the slot's rank); lost, the
    game's penalty applies for $reason (``poor_performance`` unless the game says otherwise). A quest finished after its deadline has already
    failed with ``timeout``, whatever the outcome."""
    progress = await get(session, progress_id)
    was = progress.status
    await settle(session, hooks, progress.team_id)
    await session.refresh(progress)
    if was == ACTIVE and progress.status == FAILED:
        return progress  # (ran out of time first)
    if progress.status != ACTIVE:
        raise RotationError("only a quest that was taken can be finished" if progress.status == OFFERED else f"that quest is already {progress.status}")
    if reason not in (TIMEOUT, POOR_PERFORMANCE):
        raise RotationError("a quest fails for running out of time or for poor performance")
    quest = await session.get(SlotQuest, progress.slot_quest_id)
    slot = await session.get(QuestSlot, quest.slot_id)
    now = wallclock.timestamp()
    if won:
        progress.status, progress.finished_at = COMPLETED, now
        await service.complete(session, progress.team_id, slot.key, slot.rank)
        await session.flush()
    else:
        await _fail(session, hooks, progress, quest, reason, now)
    return progress


async def active(session: AsyncSession, team_id: int, slot_key: str) -> TeamSlotQuest | None:
    """The quest of the slot the team has taken and not finished (settle first to be sure it is still in time)."""
    return await session.scalar(select(TeamSlotQuest).join(SlotQuest, SlotQuest.id == TeamSlotQuest.slot_quest_id).join(QuestSlot, QuestSlot.id == SlotQuest.slot_id).where(
        TeamSlotQuest.team_id == team_id, TeamSlotQuest.status == ACTIVE, QuestSlot.key == slot_key).limit(1))


async def forget_team(session: AsyncSession, team_id: int) -> None:
    """A team that is deleted takes its progress with it."""
    for each in (await session.scalars(select(TeamSlotQuest).where(TeamSlotQuest.team_id == team_id))).all():
        await session.execute(delete(QuestBossFight).where(QuestBossFight.progress_id == each.id))
        await session.delete(each)
    await session.flush()


def slot_view(slot: QuestSlot) -> dict:
    return {"key": slot.key, "name": slot.name, "guild": slot.guild, "rank": slot.rank}


def view(quest: SlotQuest, slot: QuestSlot, progress: TeamSlotQuest | None) -> dict:
    """What a player sees of a slot's quest and their team's hold on it. ``ends`` is when the quest rotates out (a team that already took it
    has until ``grace_ends``, at most); ``deadline`` is when the team's own time runs out once it took the quest."""
    return {**slot_view(slot), "quest": {"id": quest.id, "name": quest.name, "spec": quest.spec, "ends": quest.ends_at, "grace_ends": quest.ends_at + slot.grace_seconds,
                                          "limit_seconds": slot.limit_seconds},
            "team": None if progress is None else {"id": progress.id, "status": progress.status, "started": progress.started_at, "deadline": progress.deadline,
                                                   "failure": progress.failure}}
