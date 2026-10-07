"""Suspending a party in a town, and putting it back together when its teams are ready to leave.

``enter_town`` and ``reform`` are service functions the map drives (a party walking into a town); the team's own
actions (``ready``, ``come_back``, ``leave_party``) are what the calls use. Who may do what is decided by whoever
calls: these take ids.
"""

from dataclasses import dataclass

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights import joining, live
from ..fights.build import hero_fighter
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..relations.hooks import Relations
from ..heroes.models import Hero, Team, TeamMember
from ..maps.travel import in_fight
from ..models import Map, World
from ..world.rng import WorldRng
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


@dataclass(frozen=True)
class ActingParty:
    """The party a team acts in right now. ``party_id`` is the real party (it keeps its formation all through a town visit).
    ``team_ids`` are the teams acting together, in the party's formation order: the whole party, or, in a town, only the team's
    group (one player's teams, by default), which is then an *ethereal* party: computed from the town visit, never stored,
    and gone when the real party is put back together. ``leader_account_id`` is the player who leads those teams."""

    party_id: int
    team_ids: tuple[int, ...]
    leader_account_id: int | None
    ethereal: bool


async def acting_party(session: AsyncSession, team_id: int) -> ActingParty | None:
    """The party the team acts in (None for a team that has not entered the game: ``parties.service.play`` puts it in one)."""
    party = await parties.party_of(session, team_id)
    if party is None:
        return None
    formation = await parties.team_ids(session, party.id)
    row = await team_row(session, team_id)
    if row is None:
        return ActingParty(party.id, tuple(formation), await parties.leader_account(session, party.id), False)
    group = {each.team_id for each in await _group_of(session, row)}
    mine = tuple(each for each in formation if each in group)
    owners = {team.id: team.account_id for team in (await session.scalars(select(Team).where(Team.id.in_(mine)))).all()}
    return ActingParty(party.id, mine, owners.get(mine[0]) if mine else None, True)


async def settle(session: AsyncSession, towns: Towns, party_id: int) -> TownVisit | None:
    """A party that stands in a town (``Towns.is_town``) and is not suspended yet comes apart there; the map calls this when a
    party is formed or arrives. None if the party is not in a town (or already suspended)."""
    party = await parties.get_party(session, party_id)
    if await visit_of_party(session, party.id) is not None or not await towns.is_town(session, party.map_id, party.x, party.y):
        return None
    return await enter_town(session, towns, party.id)


async def settle_hero(session: AsyncSession, towns: Towns, hero_id: int) -> TownVisit | None:
    """``settle`` for the party the hero's team is in, for the calls that can bring a party to a town (a warp in a dialog, a
    team entering the game). Nothing happens for a hero on no team, a party in a fight, or a party that is not in a town."""
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id))
    party = None if team_id is None else await parties.party_of(session, team_id)
    if party is None or await in_fight(session, party):
        return None
    return await settle(session, towns, party.id)


async def _ready_to_fight(session: AsyncSession, rules: Rules, party: Party) -> tuple[int, int, WorldRng]:
    """What the hub reads about a party that is whole again and about to fight: its strength, how many fights it has had (so the
    same party meeting monsters twice meets different ones) and the world's streams."""
    heroes = await parties.hero_ids(session, party.id)
    fighters = [await hero_fighter(session, await session.get(Hero, hero), rules) for hero in heroes]
    number = await session.scalar(select(func.count(func.distinct(FightParticipant.fight_id))).where(FightParticipant.hero_id.in_(heroes))) or 0
    world = await session.get(World, (await session.get(Map, party.map_id)).world_id)
    return sum(rules.pxp(fighter) for fighter in fighters), number, WorldRng(world.seed)


async def _start_fight(session: AsyncSession, towns: Towns, rules: Rules, party: Party) -> FightRecord | None:
    """The fight the game's ``Towns.encounter`` picks for the party starts (None when it picks no monsters, or the fight is refused)."""
    strength, number, rng = await _ready_to_fight(session, rules, party)
    keys = await towns.encounter(session, rules, party.id, strength, rng, number)
    if not keys:
        return None
    try:
        async with session.begin_nested():
            return await live.start_party_fight(session, party.id, keys, rules)
    except live.Refused:
        return None


async def leave(session: AsyncSession, towns: Towns, rules: Rules, team_id: int) -> tuple[str, FightRecord | None]:
    """The Leave Town button: ``ready``, and when that puts the party back together, the fight the game's ``Towns.encounter``
    picks starts at once (None when it picks no monsters, or the fight is refused). Or, on a small chance
    (``Rules.join_chance``), the hub offers the party a running fight to join instead, and no fight starts until its leader answers
    (``accept_join``, ``decline_join``; ``join_offer`` says whether one stands). Returns the result of ``ready`` and the fight."""
    party = await parties.party_of(session, team_id)
    result = await ready(session, towns, team_id)
    if result != "reformed" or party is None:
        return result, None
    strength, number, rng = await _ready_to_fight(session, rules, party)
    if await joining.offer(session, rules, party.id, strength, rng.stream("town", "party", party.id, "join", number)) is not None:
        return result, None
    return result, await _start_fight(session, towns, rules, party)


async def join_offer(session: AsyncSession, team_id: int) -> bool:
    """Whether the hub has offered the team's party a running fight and waits for the leader's answer. (What fight is never said.)"""
    party = await parties.party_of(session, team_id)
    return party is not None and await joining.offer_of(session, party.id) is not None


async def _offered(session: AsyncSession, account_id: int, team_id: int) -> Party:
    """The party of the team that has an offer standing, for its leader ($account_id) to answer."""
    party = await parties.party_of(session, team_id)
    if party is None or await joining.offer_of(session, party.id) is None:
        raise TownError("the hub has offered that party nothing")
    if await parties.leader_account(session, party.id) != account_id:
        raise TownError("only the party's leader answers the hub's offer")
    return party


async def accept_join(session: AsyncSession, towns: Towns, rules: Rules, relations: Relations, account_id: int, team_id: int) -> tuple[bool, FightRecord | None]:
    """The party's leader accepts the hub's offer: the party becomes the next party of that fight and acts from its next round.
    If the fight can't take it any more (it ended, filled up or the party is out of its range now) the party's own fight starts
    as it would have. Returns whether it joined, and the fight (the one joined, or the one that started: None if none did)."""
    party = await _offered(session, account_id, team_id)
    offer = await joining.offer_of(session, party.id)
    fight_id = offer.fight_id
    await joining.withdraw(session, party.id)
    try:
        async with session.begin_nested():
            record, _number = await joining.join(session, rules, relations, fight_id, party.id)
        return True, record
    except live.Refused:
        return False, await _start_fight(session, towns, rules, party)


async def decline_join(session: AsyncSession, towns: Towns, rules: Rules, account_id: int, team_id: int) -> FightRecord | None:
    """The party's leader declines the hub's offer: the party's own fight starts as usual (None when the game picks no monsters)."""
    party = await _offered(session, account_id, team_id)
    await joining.withdraw(session, party.id)
    return await _start_fight(session, towns, rules, party)


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
