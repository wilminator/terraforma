"""Fight tags and retention.

When a fight ends it is scored for each player's team that won it (``scoring``) and recorded (``FightTeam``). Each team holds a
tag for each of four kinds, PvE and PvP overkill and underdog: a fight takes a tag when its score beats the holder's. A PvP fight
that the game judges thrown (``Rules.thrown_fight``) takes no PvP tag, and no place in an alliance's list. An alliance lists
the best ``Rules.alliance_top_size`` fights of each kind among its teams' (filled as fights end; a team that leaves takes nothing
away), and both a team (``Rules.team_retention_slots``) and an alliance (``Rules.alliance_retention_slots``) can keep fights by
hand. A finished fight that no tag, list or slot holds is deleted ``Rules.fight_keep_days`` days after it ended (``sweep``).
"""

from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..alliances.models import AllianceMember
from ..challenge.models import ChallengeEntry
from ..fights.events import Event
from ..fights.fight import Fight
from ..fights.models import (
    FightActionRecord, FightCommandRecord, FightJoinRecord, FightParticipant, FightRecord, JoinOffer, PendingDrop, PendingDropChoice,
)
from ..fights.rules import Rules
from ..monsterplay.models import MonsterSeat
from ..relations.models import RatingPrompt
from . import scoring
from .models import AllianceKept, AllianceTop, FightTeam, TeamKept, TeamTag

BATCH = 500


class TagError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(TagError):
    pass


# --- scoring a fight ---------------------------------------------------------------------------------------------------

async def record_fight(session: AsyncSession, record: FightRecord, fight: Fight, rules: Rules, events: list[Event]) -> list[scoring.TeamResult]:
    """Scores a finished fight, records each team's result, and gives the winners' scores the tags and places they earn.
    Done once per fight: a fight already recorded changes nothing (and gives back nothing)."""
    if await session.scalar(select(FightTeam.team_id).where(FightTeam.fight_id == record.id).limit(1)) is not None:
        return []
    results = scoring.team_results(fight, rules, events)
    thrown = any(result.pvp for result in results) and rules.thrown_fight(fight)
    for result in results:
        session.add(FightTeam(fight_id=record.id, team_id=result.team_id, party=result.party, pvp=result.pvp, won=result.won,
                              overkill=result.overkill, underdog=result.underdog))
    await session.flush()
    for result in results:
        if not result.won:
            continue
        for kind in result.kinds():
            score = result.score(kind)
            if score <= 0 or (result.pvp and thrown):
                continue
            await _take_team_tag(session, result.team_id, kind, record.id, score)
            await _enter_alliance_tops(session, rules, result.team_id, kind, record.id, score)
    return results


async def _take_team_tag(session: AsyncSession, team_id: int, kind: str, fight_id: int, score: float) -> None:
    held = await session.get(TeamTag, (team_id, kind))
    if held is None:
        session.add(TeamTag(team_id=team_id, kind=kind, fight_id=fight_id, score=score))
    elif score > held.score:
        held.fight_id, held.score = fight_id, score
    await session.flush()


async def _enter_alliance_tops(session: AsyncSession, rules: Rules, team_id: int, kind: str, fight_id: int, score: float) -> None:
    for alliance_id in (await session.scalars(select(AllianceMember.alliance_id).where(AllianceMember.team_id == team_id))).all():
        row = await session.get(AllianceTop, (alliance_id, kind, fight_id))
        if row is None:
            session.add(AllianceTop(alliance_id=alliance_id, kind=kind, fight_id=fight_id, team_id=team_id, score=score))
        elif score > row.score:
            row.team_id, row.score = team_id, score
        await session.flush()
        ranked = (await session.scalars(
            select(AllianceTop).where(AllianceTop.alliance_id == alliance_id, AllianceTop.kind == kind).order_by(AllianceTop.score.desc(), AllianceTop.fight_id)
        )).all()
        for extra in ranked[rules.alliance_top_size:]:
            await session.delete(extra)
        await session.flush()


# --- reading ---------------------------------------------------------------------------------------------------------------

def _result(row: FightTeam) -> dict:
    return {"fight_id": row.fight_id, "pvp": row.pvp, "won": row.won, "overkill": row.overkill, "underdog": row.underdog}


async def team_view(session: AsyncSession, rules: Rules, team_id: int) -> dict:
    """A team's tags (None for a kind it holds none of), the fights it keeps by hand, and its slots."""
    held = {row.kind: row for row in (await session.scalars(select(TeamTag).where(TeamTag.team_id == team_id))).all()}
    kept = (await session.scalars(select(FightTeam).join(TeamKept, (TeamKept.fight_id == FightTeam.fight_id) & (TeamKept.team_id == FightTeam.team_id))
                                  .where(FightTeam.team_id == team_id).order_by(FightTeam.fight_id.desc()))).all()
    return {
        "tags": {kind: ({"fight_id": held[kind].fight_id, "score": held[kind].score} if kind in held else None) for kind in scoring.KINDS},
        "kept": [_result(row) for row in kept],
        "slots": rules.team_retention_slots,
    }


async def alliance_view(session: AsyncSession, rules: Rules, alliance_id: int) -> dict:
    """An alliance's top fights of each kind, best first, the fights it keeps by hand, and its sizes."""
    rows = (await session.scalars(select(AllianceTop).where(AllianceTop.alliance_id == alliance_id).order_by(AllianceTop.score.desc(), AllianceTop.fight_id))).all()
    kept = (await session.scalars(select(AllianceKept).where(AllianceKept.alliance_id == alliance_id).order_by(AllianceKept.fight_id.desc()))).all()
    return {
        "top": {kind: [{"fight_id": row.fight_id, "team_id": row.team_id, "score": row.score} for row in rows if row.kind == kind] for kind in scoring.KINDS},
        "kept": [{"fight_id": row.fight_id, "team_id": row.team_id} for row in kept],
        "top_size": rules.alliance_top_size,
        "slots": rules.alliance_retention_slots,
    }


# --- keeping fights by hand ---------------------------------------------------------------------------------------------------

async def _fought(session: AsyncSession, team_id: int, fight_id: int) -> FightTeam:
    row = await session.get(FightTeam, (fight_id, team_id))
    if row is None:
        raise NotFound("that team has no such finished fight")
    return row


async def keep(session: AsyncSession, rules: Rules, team_id: int, fight_id: int) -> None:
    """Keeps a finished fight the team was in, in one of its slots. Keeping one already kept changes nothing."""
    await _fought(session, team_id, fight_id)
    if await session.get(TeamKept, (team_id, fight_id)) is not None:
        return
    used = await session.scalar(select(func.count()).select_from(TeamKept).where(TeamKept.team_id == team_id))
    if used >= rules.team_retention_slots:
        raise TagError(f"all {rules.team_retention_slots} of the team's slots are used: let one go first")
    session.add(TeamKept(team_id=team_id, fight_id=fight_id))
    await session.flush()


async def release(session: AsyncSession, team_id: int, fight_id: int) -> None:
    await session.execute(delete(TeamKept).where(TeamKept.team_id == team_id, TeamKept.fight_id == fight_id))


async def alliance_keep(session: AsyncSession, rules: Rules, alliance_id: int, team_id: int, fight_id: int) -> None:
    """Keeps a finished fight that $team_id was in, in one of the alliance's slots."""
    await _fought(session, team_id, fight_id)
    if await session.get(AllianceKept, (alliance_id, fight_id)) is not None:
        return
    used = await session.scalar(select(func.count()).select_from(AllianceKept).where(AllianceKept.alliance_id == alliance_id))
    if used >= rules.alliance_retention_slots:
        raise TagError(f"all {rules.alliance_retention_slots} of the alliance's slots are used: let one go first")
    session.add(AllianceKept(alliance_id=alliance_id, fight_id=fight_id, team_id=team_id))
    await session.flush()


async def alliance_release(session: AsyncSession, alliance_id: int, fight_id: int) -> None:
    await session.execute(delete(AllianceKept).where(AllianceKept.alliance_id == alliance_id, AllianceKept.fight_id == fight_id))


# --- deleting what nobody holds -----------------------------------------------------------------------------------------------

def held_fights():
    """The ids of the fights that a tag, a list or a slot holds."""
    return (
        select(TeamTag.fight_id).union(select(TeamKept.fight_id), select(AllianceTop.fight_id), select(AllianceKept.fight_id))
    )


#: Every table with a link to ``fights`` and what the sweep does with its rows: delete them with the fight, or (a ledger
#: that must outlast it) let go of the fight. A test checks that none is missing.
DELETED_WITH_THE_FIGHT = {
    PendingDropChoice.__tablename__: None,  # through its pending drop
    PendingDrop.__tablename__: PendingDrop,
    FightParticipant.__tablename__: FightParticipant,
    FightActionRecord.__tablename__: FightActionRecord,
    FightCommandRecord.__tablename__: FightCommandRecord,
    FightJoinRecord.__tablename__: FightJoinRecord,
    JoinOffer.__tablename__: JoinOffer,
    RatingPrompt.__tablename__: RatingPrompt,
    FightTeam.__tablename__: FightTeam,
    MonsterSeat.__tablename__: MonsterSeat,
}
LET_GO = {ChallengeEntry.__tablename__: ChallengeEntry}


async def sweep(session: AsyncSession, rules: Rules, now: int) -> int:
    """Deletes the finished fights that nothing holds and that ended more than ``Rules.fight_keep_days`` days ago (0: kept for good), with
    what belongs to them. Returns how many fights it deleted."""
    if not rules.fight_keep_days:
        return 0
    cutoff = wallclock.now() - timedelta(days=rules.fight_keep_days)
    # Ids first, in batches: MySQL refuses a DELETE whose subquery reads the table it deletes from.
    old = (await session.scalars(
        select(FightRecord.id).where(FightRecord.finished.is_(True), FightRecord.updated_at <= cutoff, FightRecord.id.not_in(held_fights())).order_by(FightRecord.id)
    )).all()
    for start in range(0, len(old), BATCH):
        ids = old[start : start + BATCH]
        drops = select(PendingDrop.id).where(PendingDrop.fight_id.in_(ids))
        await session.execute(delete(PendingDropChoice).where(PendingDropChoice.pending_id.in_(drops)))
        for table in (PendingDrop, FightParticipant, FightActionRecord, FightCommandRecord, FightJoinRecord, JoinOffer, RatingPrompt, FightTeam, MonsterSeat):
            await session.execute(delete(table).where(table.fight_id.in_(ids)))
        await session.execute(update(ChallengeEntry).where(ChallengeEntry.fight_id.in_(ids)).values(fight_id=None))
        await session.execute(delete(FightRecord).where(FightRecord.id.in_(ids)))
    return len(old)
