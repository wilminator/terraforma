"""Suspending a party in a town, and putting it back together when its teams are ready to leave.

``enter_town`` and ``reform`` are service functions the map drives (a party walking into a town); the team's own
actions (``ready``, ``come_back``, ``leave_party``) are what the calls use. Who may do what is decided by whoever
calls: these take ids.
"""

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero
from ..parties import service as parties
from ..parties.models import Party, PartyTeam
from .hooks import Towns
from .models import TownNotice, TownTeam, TownVisit


class TownError(ValueError):
    """Something the caller can fix: the message says what."""


async def visit_of_party(session: AsyncSession, party_id: int) -> TownVisit | None:
    return await session.scalar(select(TownVisit).where(TownVisit.party_id == party_id))


async def team_row(session: AsyncSession, team_id: int) -> TownTeam | None:
    return await session.scalar(select(TownTeam).where(TownTeam.team_id == team_id))


async def is_suspended(session: AsyncSession, party_id: int) -> bool:
    return await visit_of_party(session, party_id) is not None


async def enter_town(session: AsyncSession, towns: Towns, party_id: int) -> TownVisit:
    """The party comes into a town: it is suspended and comes apart into the teams' groups (``Towns.groups``)."""
    party = await parties.get_party(session, party_id)
    if await visit_of_party(session, party.id) is not None:
        raise TownError("that party is already in a town")
    formation = await parties.team_ids(session, party.id)
    groups = await towns.groups(session, party.id, list(formation))
    if sorted(team for group in groups for team in group) != sorted(formation):
        raise TownError("the game's groups must hold every team of the party exactly once")
    visit = TownVisit(party_id=party.id, formation=list(formation))
    session.add(visit)
    await session.flush()
    for number, group in enumerate(groups):
        for team_id in group:
            session.add(TownTeam(visit_id=visit.id, team_id=team_id, group=number, waiting=False))
    await session.flush()
    return visit


async def _group_of(session: AsyncSession, row: TownTeam) -> list[TownTeam]:
    return list((await session.scalars(select(TownTeam).where(TownTeam.visit_id == row.visit_id, TownTeam.group == row.group))).all())


async def ready(session: AsyncSession, towns: Towns, team_id: int) -> str:
    """The team (with its group) goes to wait for the rest of its party to leave. The other teams are told. Returns
    ``"reformed"`` if that was the last to be ready and the party is whole again, otherwise ``"waiting"``."""
    row = await team_row(session, team_id)
    if row is None:
        raise TownError("that team is not in a town")
    if row.waiting:
        return "waiting"
    group = await _group_of(session, row)
    for member in group:
        reason = await towns.may_wait(session, member.team_id)
        if reason:
            raise TownError(reason)
    for member in group:
        member.waiting = True
    await session.flush()
    in_group = {member.team_id for member in group}
    others = (await session.scalars(select(TownTeam).where(TownTeam.visit_id == row.visit_id, TownTeam.waiting.is_(False)))).all()
    for other in others:
        if other.team_id not in in_group:
            for member in group:
                session.add(TownNotice(team_id=other.team_id, about_team_id=member.team_id))
    await session.flush()
    return await _reform_if_all_ready(session, row.visit_id)


async def come_back(session: AsyncSession, team_id: int) -> None:
    """A waiting team (with its group) goes back into the town: no longer waiting, and nobody is waiting on it."""
    row = await team_row(session, team_id)
    if row is None:
        raise TownError("that team is not in a town")
    group = await _group_of(session, row)
    ids = [member.team_id for member in group]
    for member in group:
        member.waiting = False
    await session.execute(delete(TownNotice).where(TownNotice.about_team_id.in_(ids)))
    await session.flush()


async def leave_party(session: AsyncSession, team_id: int) -> None:
    """The team leaves the party for good (it is no longer part of the town visit). If everyone left is waiting,
    the party is whole again."""
    row = await team_row(session, team_id)
    if row is None:
        raise TownError("that team is not in a town")
    visit_id = row.visit_id
    await session.execute(delete(TownNotice).where((TownNotice.team_id == team_id) | (TownNotice.about_team_id == team_id)))
    await session.delete(row)
    await session.flush()
    party_id = await parties.leave_party(session, team_id)
    if party_id is None or not await session.scalar(select(func.count()).select_from(PartyTeam).where(PartyTeam.party_id == party_id)):
        await session.execute(delete(TownVisit).where(TownVisit.id == visit_id))
        return
    await _reform_if_all_ready(session, visit_id)


async def _reform_if_all_ready(session: AsyncSession, visit_id: int) -> str:
    rows = (await session.scalars(select(TownTeam).where(TownTeam.visit_id == visit_id))).all()
    if not rows or not all(row.waiting for row in rows):
        return "waiting"
    visit = await session.get(TownVisit, visit_id)
    await reform(session, visit.party_id)
    return "reformed"


async def reform(session: AsyncSession, party_id: int) -> None:
    """The party is put back as one unit, in the formation it had (the teams that are still in it, in their order), at
    its place on the map: every hero of it is placed there. The town visit and its notices are gone."""
    visit = await visit_of_party(session, party_id)
    if visit is None:
        raise TownError("that party is not in a town")
    party = await session.get(Party, party_id)
    heroes = await parties.hero_ids(session, party_id)
    if heroes:
        await session.execute(update(Hero).where(Hero.id.in_(heroes)).values(map_id=party.map_id, x=party.x, y=party.y))
    team_ids = [row.team_id for row in (await session.scalars(select(TownTeam).where(TownTeam.visit_id == visit.id))).all()]
    await session.execute(delete(TownNotice).where(TownNotice.team_id.in_(team_ids)))
    await session.execute(delete(TownTeam).where(TownTeam.visit_id == visit.id))
    await session.execute(delete(TownVisit).where(TownVisit.id == visit.id))
    await session.flush()


async def view(session: AsyncSession, team_id: int) -> dict:
    """What a team sees of its town visit: whether it is in one, its own state, the other teams' and who is ready."""
    row = await team_row(session, team_id)
    if row is None:
        return {"in_town": False}
    visit = await session.get(TownVisit, row.visit_id)
    rows = (await session.scalars(select(TownTeam).where(TownTeam.visit_id == row.visit_id))).all()
    order = {team: number for number, team in enumerate(visit.formation)}
    notices = (await session.scalars(select(TownNotice).where(TownNotice.team_id == team_id).order_by(TownNotice.id))).all()
    return {
        "in_town": True, "party_id": visit.party_id, "waiting": row.waiting, "group": row.group,
        "teams": [{"team": other.team_id, "group": other.group, "waiting": other.waiting}
                  for other in sorted(rows, key=lambda each: order.get(each.team_id, 0))],
        "ready": [notice.about_team_id for notice in notices],
    }
