"""The nearby list: what a hero can reach for an action, nearest first, NPCs before parties."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.rules import Rules
from ..heroes.models import Hero, Team, TeamMember
from ..models import Map
from ..npcs.models import Npc
from ..parties import service as parties
from ..parties.models import Party
from ..profiles.models import TeamProfile
from ..towns import service as towns
from .hooks import Reach, distance


async def party_of_hero(session: AsyncSession, hero: Hero) -> Party | None:
    """The party the hero's team is in (a hero is on at most one team)."""
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    return None if team_id is None else await parties.party_of(session, team_id)


def _axis(at: int, window: int, size: int | None, wrap: bool) -> list[int] | None:
    """The values of one coordinate within $window of $at, or None when that is every tile of the map's side."""
    if size is None or 2 * window + 1 >= size:
        return None
    values = range(at - window, at + window + 1)
    return sorted({value % size for value in values} if wrap else {value for value in values if 0 <= value < size})


async def _candidate_parties(session: AsyncSession, reach: Reach, hero: Hero, mine: Party | None) -> list[Party]:
    query = select(Party).where(Party.map_id == hero.map_id)
    if mine is not None:
        query = query.where(Party.id != mine.id)
    if reach.party_window is not None:
        found = await session.get(Map, hero.map_id)
        for column, at, size, wrap in ((Party.x, hero.x, found.width, found.wrap_x), (Party.y, hero.y, found.height, found.wrap_y)):
            values = _axis(at, reach.party_window, size, wrap)
            if values is not None:
                query = query.where(column.in_(values))
    return list((await session.scalars(query.order_by(Party.id))).all())


async def _shown_teams(session: AsyncSession, party_id: int) -> list[dict]:
    """The party's teams that their players made visible: a hidden team is not named, it is just part of the party."""
    rows = await session.execute(
        select(Team.id, Team.name).join(TeamProfile, TeamProfile.team_id == Team.id)
        .where(TeamProfile.visible.is_(True), Team.id.in_(await parties.team_ids(session, party_id)))
    )
    order = {team_id: place for place, team_id in enumerate(await parties.team_ids(session, party_id))}
    return [{"id": team_id, "name": name} for team_id, name in sorted(rows.all(), key=lambda row: order[row[0]])]


async def nearby(session: AsyncSession, reach: Reach, rules: Rules, hero: Hero, action: str) -> dict:
    """What the hero can reach for $action from where they stand: the NPCs, then (when the game's ``Reach.lists_parties``
    says so) the other parties, each nearest first, at most ``Rules.nearby_limit`` in all, and for how many seconds the list
    may be shown before asking again. An entry is ``{"kind": "npc", "id", "key", "name", "distance"}`` or
    ``{"kind": "party", "id", "teams": [{"id", "name"}], "distance"}`` (only the visible teams are named)."""
    found = await session.get(Map, hero.map_id)
    here = (hero.x, hero.y)
    entries: list[dict] = []
    rows = (await session.scalars(select(Npc).where(Npc.map_id == hero.map_id).order_by(Npc.name, Npc.id))).all()
    near = []
    for npc in rows:
        if await reach.npc(session, action, hero, npc) is None:
            near.append((distance(found, here, (npc.x, npc.y)), npc))
    near.sort(key=lambda pair: (pair[0], pair[1].name, pair[1].id))
    entries += [{"kind": "npc", "id": npc.id, "key": npc.key, "name": npc.name, "distance": away} for away, npc in near]
    if await reach.lists_parties(session, action, hero):
        mine = await party_of_hero(session, hero)
        close = []
        for party in await _candidate_parties(session, reach, hero, mine):
            if await towns.visit_of_party(session, party.id) is None and await reach.party(session, action, hero, party) is None:
                close.append((distance(found, here, (party.x, party.y)), party))
        close.sort(key=lambda pair: (pair[0], pair[1].id))
        entries += [{"kind": "party", "id": party.id, "teams": await _shown_teams(session, party.id), "distance": away} for away, party in close]
    return {"action": action, "valid_for": reach.valid_for(action), "nearby": entries[: rules.nearby_limit]}
