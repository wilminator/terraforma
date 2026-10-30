"""The quests the world needs: finds where players have nothing to progress on, and makes a quest for it out of monsters the world already has.

The game lists bands of party strength (``Needs.bands``). A band has a gap when no quest of its rotating slot fits it, or when none of its
teams completed a quest for the band's stall time. The engine then promotes an existing monster (or a gang) to a boss scaled to the band
(``quests.bosses``) and records a ``QuestProposal``. The game says whether that waits for an admin or goes live at once; live, the quest is a
template of the band's slot, which offers it from its next period (``quests.rotation``). It runs from housekeeping, so any server may run it:
a band keeps one open proposal at a time, which the database enforces.
"""

import logging
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..fights.rules import Rules
from ..guild.ranks import party_pxp
from ..heroes.models import Team, TeamMember
from ..models import World
from ..realms import supply
from ..realms.models import LIVE as TOWN_LIVE
from ..realms.models import Material, Settlement, SettlementSupply
from ..world.rng import WorldRng
from ..world.start import ensure_start
from . import bosses, service
from .hooks import Band, Needs
from .models import QuestRecord
from .needs_models import LIVE, NO_FIT, PROPOSED, REJECTED, RETIRED, STALLED, SUPPLY, QuestProposal
from .weekly import check_name, check_spec
from .weekly_models import NEEDS, QuestTemplate

log = logging.getLogger(__name__)


class _Skip(Exception):
    """The game's dungeon failed, so this band is left for the next run."""


class NeedsError(service.QuestError):
    """Something the caller can fix: the message says what."""


def _seconds(moment: datetime) -> int:
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp())


async def team_strengths(session: AsyncSession, rules: Rules) -> dict[int, int]:
    """Every team that has a hero, and its strength: the PXP of its heroes added up."""
    ids = (await session.scalars(select(TeamMember.team_id).distinct().order_by(TeamMember.team_id))).all()
    return {team_id: await party_pxp(session, rules, [team_id]) for team_id in ids}


def in_band(band: Band, pxp: int) -> bool:
    return pxp >= band.pxp_min and (not band.pxp_max or pxp <= band.pxp_max)


async def fits(session: AsyncSession, band: Band) -> bool:
    """Whether the band's slot has an active quest meant for a strength that overlaps the band's (templates that say nothing about
    strength do not count: they could be meant for anyone)."""
    for each in (await session.scalars(select(QuestTemplate).where(QuestTemplate.active.is_(True), QuestTemplate.slot == band.slot))).all():
        if not (each.pxp_min or each.pxp_max):
            continue
        if (not each.pxp_max or each.pxp_max >= band.pxp_min) and (not band.pxp_max or each.pxp_min <= band.pxp_max):
            return True
    return False


async def stalled(session: AsyncSession, band: Band, team_ids: list[int], now: int) -> bool:
    """Whether none of the teams progressed within the band's stall time: each team's last quest completion (or, before any, the day it was made)."""
    if not band.stall_seconds:
        return False
    last = {team_id: moment for team_id, moment in (await session.execute(
        select(QuestRecord.team_id, func.max(QuestRecord.updated_at)).where(QuestRecord.team_id.in_(team_ids)).group_by(QuestRecord.team_id))).all()}
    made = {team_id: moment for team_id, moment in (await session.execute(select(Team.id, Team.created_at).where(Team.id.in_(team_ids)))).all()}
    return all(_seconds(last.get(team_id) or made[team_id]) < now - band.stall_seconds for team_id in team_ids)


async def run(session: AsyncSession, hooks: Needs, rules: Rules) -> list[QuestProposal]:
    """Looks at every band the game lists and makes a quest for each that has a gap and no open proposal. Returns the proposals made."""
    bands = hooks.bands()
    world = await session.get(World, (await ensure_start(session)).world_id)
    made = await supply_gaps(session, hooks, world)
    if not bands:
        return made
    now = wallclock.timestamp()
    strengths = await team_strengths(session, rules)
    for band in bands:
        if await session.scalar(select(QuestProposal.id).where(QuestProposal.band == band.key, QuestProposal.open == 1)) is not None:
            continue
        team_ids = [team_id for team_id, pxp in strengths.items() if in_band(band, pxp)]
        if len(team_ids) < max(1, band.min_teams):
            continue
        if not await fits(session, band):
            reason = NO_FIT
        elif await stalled(session, band, team_ids, now):
            reason = STALLED
        else:
            continue
        serial = await session.scalar(select(func.count()).select_from(QuestProposal).where(QuestProposal.band == band.key))
        rng = WorldRng(world.seed).stream("needs", band.key, serial)
        found = await bosses.promote(session, hooks, rules, band, rng)
        if found is None:
            log.warning("quest needs: no monster the world has can be promoted for band %s", band.key)
            continue
        name = check_name(hooks.name(rng, band, [boss["monster"] for boss in found]))
        spec = {"bosses": found, "band": band.key, "reason": reason}
        proposal = QuestProposal(band=band.key, slot=band.slot, pxp_min=band.pxp_min, pxp_max=band.pxp_max, reason=reason, name=name, spec=spec)
        try:
            async with session.begin_nested():  # (the game's dungeon is undone with the proposal if another server won the band)
                try:
                    if (dungeon := await hooks.dungeon(session, rng, band, found)) is not None:
                        spec["dungeon"] = dungeon
                    check_spec(spec)
                except Exception:  # (a game's mistake must not stop the other bands: the nested transaction is rolled back)
                    log.exception("quest needs: the game's dungeon for band %s failed", band.key)
                    raise _Skip from None
                session.add(proposal)
                await session.flush()
        except _Skip:
            continue
        except IntegrityError:  # (another server made one just now)
            continue
        if not hooks.needs_approval(band):
            await approve(session, proposal.id)
        made.append(proposal)
    return made


def _percent(level: int, capacity: int) -> int:
    return level * 100 // capacity if capacity > 0 else 0


async def supply_gaps(session: AsyncSession, hooks: Needs, world: World) -> list[QuestProposal]:
    """The supply half of a run. A live settlement whose supply of a material is below the game's floor (``Needs.supply_floor``) and has a slot
    to go in (``Needs.supply_slot``) gets a quest to bring more, one open proposal at a time for each settlement and material. An open supply
    proposal whose gap has closed (the supply recovered, or the settlement fell) is taken back: rejected while it waits, retired when live.
    The levels are read at the world clock without being changed. Returns the proposals made."""
    open_ones = {each.band: each for each in (await session.scalars(
        select(QuestProposal).where(QuestProposal.reason == SUPPLY, QuestProposal.open == 1))).all()}
    rows = (await session.execute(
        select(Settlement, Material, SettlementSupply).join(SettlementSupply, SettlementSupply.settlement_id == Settlement.id)
        .join(Material, Material.id == SettlementSupply.material_id)
        .where(Settlement.world_id == world.id, Settlement.state == TOWN_LIVE).order_by(Settlement.id, Material.id))).all()
    made, short = [], set()
    for settlement, material, row in rows:
        floor, key = hooks.supply_floor(settlement, material), f"supply-{settlement.id}-{material.id}"
        level = supply.level_at(row, world.tick)
        if not floor or _percent(level, row.capacity) >= floor:
            continue
        short.add(key)
        slot = hooks.supply_slot(settlement, material)
        if key in open_ones or not slot:
            continue
        serial = await session.scalar(select(func.count()).select_from(QuestProposal).where(QuestProposal.band == key))
        rng = WorldRng(world.seed).stream("needs", key, serial)
        amount = hooks.supply_amount(settlement, material, level, row.capacity)
        spec = {"supply": {"settlement": settlement.key, "material": material.key, "amount": amount}, "band": key, "reason": SUPPLY}
        if reward := hooks.supply_reward(rng, settlement, material, amount):
            spec["reward"] = reward
        proposal = QuestProposal(band=key, slot=slot, pxp_min=0, pxp_max=0, reason=SUPPLY, name=check_name(hooks.supply_name(rng, settlement, material)),
                                 spec=check_spec(spec))
        try:
            async with session.begin_nested():
                session.add(proposal)
                await session.flush()
        except IntegrityError:  # (another server made one just now)
            continue
        if not hooks.needs_approval(Band(key, slot, 0, 0)):
            await approve(session, proposal.id)
        made.append(proposal)
    for key, proposal in open_ones.items():
        if key not in short:
            await (retire if proposal.status == LIVE else reject)(session, proposal.id, hooks)
    return made


async def deliver(session: AsyncSession, spec: dict, amount: int | None = None) -> int:
    """Brings a supply quest's goods in: adds $amount (what the quest asked, if left out) of its material to its settlement, and returns how
    much fitted under the capacity. $spec is the quest's (``spec["supply"]``); a game's dialog or reward step calls this when the players finish
    the quest. A settlement that is no longer live takes nothing."""
    goal = spec.get("supply") if isinstance(spec, dict) else None
    if not isinstance(goal, dict):
        raise NeedsError("that quest brings nothing to a settlement")
    world_id = (await ensure_start(session)).world_id
    town = await session.scalar(select(Settlement).where(Settlement.world_id == world_id, Settlement.key == goal["settlement"]))
    if town is None or town.state != TOWN_LIVE:
        return 0
    return await supply.add(session, town, goal["material"], goal["amount"] if amount is None else amount)


async def get(session: AsyncSession, proposal_id: int) -> QuestProposal:
    proposal = await session.get(QuestProposal, proposal_id)
    if proposal is None:
        raise NeedsError("there's no such quest proposal")
    return proposal


async def approve(session: AsyncSession, proposal_id: int) -> QuestProposal:
    """Makes the proposal live: a template of the band's slot (key ``need-<id>``), which the slot offers from its next period."""
    proposal = await get(session, proposal_id)
    if proposal.status != PROPOSED:
        raise NeedsError(f"that proposal is already {proposal.status}")
    template = QuestTemplate(key=f"need-{proposal.id}", name=proposal.name, source=NEEDS, min_level=1, max_level=999, weight=1, spec=proposal.spec,
                             active=True, slot=proposal.slot, pxp_min=proposal.pxp_min, pxp_max=proposal.pxp_max)
    session.add(template)
    await session.flush()
    proposal.status, proposal.template_id = LIVE, template.id
    await session.flush()
    return proposal


async def _dungeon_gone(session: AsyncSession, hooks: Needs | None, proposal: QuestProposal) -> None:
    """Tells the game its dungeon for the proposal is no longer wanted (only when there is one and the caller has the game's hooks)."""
    if hooks is not None and isinstance(proposal.spec, dict) and proposal.spec.get("dungeon") is not None:
        await hooks.dungeon_gone(session, proposal.id, proposal.spec["dungeon"])


async def reject(session: AsyncSession, proposal_id: int, hooks: Needs | None = None) -> QuestProposal:
    """Turns a proposal down; the band may get a new one at the next run if the gap is still there."""
    proposal = await get(session, proposal_id)
    if proposal.status != PROPOSED:
        raise NeedsError(f"that proposal is already {proposal.status}")
    proposal.status, proposal.open = REJECTED, None
    await session.flush()
    await _dungeon_gone(session, hooks, proposal)
    return proposal


async def retire(session: AsyncSession, proposal_id: int, hooks: Needs | None = None) -> QuestProposal:
    """Takes a live quest out of its slot (its template goes inactive; teams that took it keep it). The band may get a new proposal."""
    proposal = await get(session, proposal_id)
    if proposal.status != LIVE:
        raise NeedsError("only a live quest can be retired" if proposal.status == PROPOSED else f"that proposal is already {proposal.status}")
    (await session.get(QuestTemplate, proposal.template_id)).active = False
    proposal.status, proposal.open = RETIRED, None
    await session.flush()
    await _dungeon_gone(session, hooks, proposal)
    return proposal


async def proposals(session: AsyncSession, status: str | None = None, after: int = 0, limit: int = 50) -> list[QuestProposal]:
    query = select(QuestProposal).where(QuestProposal.id > after)
    if status is not None:
        query = query.where(QuestProposal.status == status)
    return list((await session.scalars(query.order_by(QuestProposal.id).limit(limit))).all())


def view(proposal: QuestProposal) -> dict:
    return {"id": proposal.id, "band": proposal.band, "slot": proposal.slot, "pxp_min": proposal.pxp_min, "pxp_max": proposal.pxp_max, "reason": proposal.reason,
            "name": proposal.name, "status": proposal.status, "template_id": proposal.template_id, "spec": proposal.spec}
