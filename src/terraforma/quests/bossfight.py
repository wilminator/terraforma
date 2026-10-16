"""The boss fight of a rotating quest the engine made (``quests.needs``): a party fights the bosses the quest's ``spec`` holds, and the quest is
settled by how that fight ended.

A dialog (``slot_boss`` tag) starts the fight, usually from the dungeon's last room, and asks about it afterwards. The fight is the ordinary
one (``fights.live``) against the promoted monsters (``quests.bosses``); a ``QuestBossFight`` row ties it to the progress of every team of the party
that holds the same quest. It is settled when someone asks, not when the fight ends, so the fight code knows nothing of quests: a team that
won it finishes the quest as won; a team that did not (all down, fled, or off the fight's scoring) finishes it as failed with ``poor_performance``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights import live
from ..fights.models import FightRecord
from ..fights.rules import Rules
from ..fighttags.models import FightTeam
from . import bosses, rotation
from .boss_models import QuestBossFight
from .hooks import RotatingQuests
from .rotation_models import POOR_PERFORMANCE, SlotQuest, TeamSlotQuest

#: The two results a boss fight can have for a team.
WON, LOST = "won", "lost"


class BossFightError(rotation.RotationError):
    """Something the caller can fix: the message says what."""


async def _held(session: AsyncSession, hooks: RotatingQuests, team_id: int, slot_key: str) -> tuple[TeamSlotQuest, SlotQuest] | None:
    """The team's taken quest of the slot (in time) and the period's quest it is."""
    await rotation.settle(session, hooks, team_id)
    progress = await rotation.active(session, team_id, slot_key)
    return None if progress is None else (progress, await session.get(SlotQuest, progress.slot_quest_id))


def entries_of(quest: SlotQuest) -> list[dict]:
    """The boss entries of a quest's spec: empty for a quest the engine did not make (a hand-made or generated one)."""
    found = quest.spec.get("bosses") if isinstance(quest.spec, dict) else None
    return [entry for entry in found if isinstance(entry, dict) and "monster" in entry and "scale" in entry] if isinstance(found, list) else []


async def start(session: AsyncSession, hooks: RotatingQuests, rules: Rules, party_id: int, team_ids: tuple[int, ...], slot_key: str, team_id: int) -> FightRecord | None:
    """The party fights the bosses of the quest $team_id took from the slot. Every team in $team_ids that took the same quest of the slot is tied to
    the fight. None when the team has no such quest or it has no bosses (not an engine-made one); raises ``live.Refused`` when the party cannot
    fight now (a hero already in a fight, no heroes)."""
    held = await _held(session, hooks, team_id, slot_key)
    if held is None or not (entries := entries_of(held[1])):
        return None
    try:
        foes = await bosses.fighters(session, rules, entries)
    except bosses.BossError as error:
        raise BossFightError(str(error)) from error
    record = await live.start_party_fight(session, party_id, [], rules, foes=foes)
    for each in team_ids:
        mine = await _held(session, hooks, each, slot_key)
        if mine is not None and mine[0].slot_quest_id == held[0].slot_quest_id:
            session.add(QuestBossFight(fight_id=record.id, progress_id=mine[0].id))
    await session.flush()
    return record


async def result(session: AsyncSession, hooks: RotatingQuests, team_id: int, slot_key: str) -> str | None:
    """How the team's latest boss fight for its taken quest of the slot ended, as ``WON`` or ``LOST``; None when there is none yet or it is still
    running (or the team has no such quest in time)."""
    held = await _held(session, hooks, team_id, slot_key)
    if held is None:
        return None
    record = await session.scalar(
        select(FightRecord).join(QuestBossFight, QuestBossFight.fight_id == FightRecord.id).where(QuestBossFight.progress_id == held[0].id).order_by(QuestBossFight.id.desc()).limit(1))
    if record is None or not record.finished:
        return None
    won = await session.scalar(select(FightTeam.won).where(FightTeam.fight_id == record.id, FightTeam.team_id == team_id))
    return WON if won else LOST


async def settle(session: AsyncSession, hooks: RotatingQuests, team_id: int, slot_key: str, wanted: str) -> bool:
    """Finishes the team's quest by the boss fight's result if that result is $wanted (``WON`` or ``LOST``): won, or failed with
    ``poor_performance``. False (and nothing changes) when there is no result yet or it is the other one."""
    if await result(session, hooks, team_id, slot_key) != wanted:
        return False
    progress = await rotation.active(session, team_id, slot_key)
    await rotation.finish(session, hooks, progress.id, wanted == WON, POOR_PERFORMANCE)
    return True


__all__ = ["BossFightError", "LOST", "WON", "entries_of", "result", "settle", "start"]
