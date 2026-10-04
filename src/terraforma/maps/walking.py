"""Walking a party over a map: the client proposes a route, the server checks it and then each step.

A party's leader proposes a route as a list of tiles to pass or as a destination. ``plan`` checks it (every tile on the map
and passable, no party in the way that ``Rules.party_blocks`` says blocks) and fills the gaps between tiles that are not
neighbours with the shortest way round, then keeps the route it came to on the party and returns it, the tile the party
stands on first. The client then walks it, calling ``step`` as it arrives on each tile in turn. A step the server doesn't
confirm (the tile isn't the next of the route, the map changed, a party stands in the way, the party is in a fight) ends
the route and says where the party really is: the client goes back to that tile.

Each confirmed step moves the party and ends with a roll for monsters: the tile's ``encounter_rate`` against a stream of the
world's seed for that party and that step, once the party has taken more than the map's ``safe_steps`` since it entered
the map or last fought. A hit picks one of the zone's encounters (by weight), starts the fight where the party stands and
ends the route. A step through a wrapped edge is a step like any other. Steps are one tile up, down, left or right.
"""

from collections import deque

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.schema import ENCOUNTER_SCALE
from ..fights import live
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..models import Map, World
from ..parties import service as parties
from ..parties.models import Party
from ..towns import service as towns
from ..world.rng import WorldRng

Tile = tuple[int, int]


class WalkError(ValueError):
    """The route or the step can't be (409)."""


class NotFound(WalkError):
    pass


class NotYours(WalkError):
    pass


def find_path(game_map: Map, start: Tile, goal: Tile, blocked: frozenset[Tile] | set[Tile], limit: int) -> list[Tile] | None:
    """The shortest way from $start to $goal over passable tiles that are not $blocked, as the tiles after $start (wrapped
    edges as the map wraps them), or None if there is none in $limit steps or fewer."""
    if start == goal:
        return []
    came: dict[Tile, Tile] = {start: start}
    queue = deque([(start, 0)])
    while queue:
        here, steps = queue.popleft()
        if steps == limit:
            continue
        for dx, dy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
            spot = game_map.normalize(here[0] + dx, here[1] + dy)
            if spot is None or spot in came or spot in blocked or not game_map.tile(*spot)["passable"]:
                continue
            came[spot] = here
            if spot == goal:
                way = [spot]
                while way[-1] != start and came[way[-1]] != start:
                    way.append(came[way[-1]])
                return way[::-1]
            queue.append((spot, steps + 1))
    return None


async def _map_of(session: AsyncSession, party: Party) -> Map:
    return await session.get(Map, party.map_id)


async def own_party(session: AsyncSession, account_id: int, party_id: int) -> Party:
    """The party, if $account_id leads it (only the leader moves a party). Raises NotFound or NotYours."""
    try:
        party = await parties.get_party(session, party_id)
    except parties.NotFound as error:
        raise NotFound(str(error)) from error
    if await parties.leader_account(session, party.id) != account_id:
        raise NotYours("only the party's leader can move it")
    return party


async def _blocked(session: AsyncSession, rules: Rules, party: Party) -> set[Tile]:
    """The tiles of the party's map that another party stands on and keeps this one off."""
    others = (await session.scalars(select(Party).where(Party.map_id == party.map_id, Party.id != party.id))).all()
    return {(other.x, other.y) for other in others if rules.party_blocks(party, other)}


async def _in_fight(session: AsyncSession, party: Party) -> bool:
    heroes = await parties.hero_ids(session, party.id)
    return heroes != [] and await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_(heroes), FightRecord.finished.is_(False)).limit(1)
    ) is not None


async def _check_free(session: AsyncSession, party: Party) -> None:
    if await _in_fight(session, party):
        raise WalkError("a party in a fight can't walk")
    if await towns.is_suspended(session, party.id):
        raise WalkError("a party in a town is apart: it must be put back together first")


def _view(party: Party, game_map: Map) -> dict:
    route = party.route
    return {
        "map": game_map.name, "revision": game_map.revision, "x": party.x, "y": party.y,
        "route": route["tiles"] if route else None, "at": route["at"] if route else None,
    }


async def where(session: AsyncSession, account_id: int, party_id: int) -> dict:
    """Where the party is and the route it is on, if any."""
    party = await own_party(session, account_id, party_id)
    return _view(party, await _map_of(session, party))


async def plan(session: AsyncSession, rules: Rules, account_id: int, party_id: int, waypoints: list[Tile]) -> dict:
    """Gives the party the route through $waypoints (a destination is one waypoint), from where it stands, and returns it.
    Raises WalkError if a tile is off the map or can't be stepped on, there is no way to one in the party's
    ``Rules.route_limit``, or the party is where it was asked to go."""
    party = await own_party(session, account_id, party_id)
    await _check_free(session, party)
    game_map = await _map_of(session, party)
    blocked = await _blocked(session, rules, party)
    tiles: list[Tile] = [(party.x, party.y)]
    for waypoint in waypoints:
        spot = game_map.normalize(*waypoint)
        if spot is None:
            raise WalkError(f"({waypoint[0]}, {waypoint[1]}) is off the map")
        if not game_map.tile(*spot)["passable"]:
            raise WalkError(f"({spot[0]}, {spot[1]}) can't be walked on")
        if spot in blocked:
            raise WalkError(f"({spot[0]}, {spot[1]}) is taken by another party")
        way = find_path(game_map, tiles[-1], spot, blocked, rules.route_limit - (len(tiles) - 1))
        if way is None:
            raise WalkError(f"there's no way to ({spot[0]}, {spot[1]}) of {rules.route_limit} steps or fewer")
        tiles += way
    if len(tiles) == 1:
        raise WalkError("the party is there already")
    party.route = {"tiles": [list(tile) for tile in tiles], "at": 0, "revision": game_map.revision}
    await session.flush()
    return _view(party, game_map)


async def _stop(session: AsyncSession, party: Party, game_map: Map, why: str) -> dict:
    """The step wasn't confirmed: the route ends and the party is told where it really is."""
    party.route = None
    await session.flush()
    return {"confirmed": False, "reason": why, "map": game_map.name, "revision": game_map.revision, "x": party.x, "y": party.y}


async def step(session: AsyncSession, rules: Rules, account_id: int, party_id: int, x: int, y: int) -> dict:
    """The client has arrived on (x, y), the next tile of the party's route. Confirms it (the party moves there and may meet
    monsters: ``fight`` is then the fight's number and ``monsters`` its keys, and the route is over) or, if it can't, says
    where the party stands (``confirmed`` false) and ends the route. ``done`` is true on the route's last tile."""
    party = await own_party(session, account_id, party_id)
    game_map = await _map_of(session, party)
    route = party.route
    if not route:
        return await _stop(session, party, game_map, "the party has no route")
    if route["revision"] != game_map.revision:
        return await _stop(session, party, game_map, "the map has changed")
    following = route["tiles"][route["at"] + 1]
    if [x, y] != following:
        return await _stop(session, party, game_map, "that isn't the next tile of the route")
    try:
        await _check_free(session, party)
    except WalkError as error:
        return await _stop(session, party, game_map, str(error))
    if (x, y) in await _blocked(session, rules, party):
        return await _stop(session, party, game_map, "another party is in the way")

    party.x, party.y = x, y
    party.steps += 1
    party.walked += 1
    at = route["at"] + 1
    result = {"confirmed": True, "map": game_map.name, "revision": game_map.revision, "x": x, "y": y, "done": at == len(route["tiles"]) - 1}
    record, monsters = await _meet(session, rules, party, game_map)
    if record is not None:
        result |= {"fight": record.id, "monsters": monsters, "done": True}
    if result["done"]:
        party.route = None
    else:
        party.route = {**route, "at": at}
    await session.flush()
    return result


async def _meet(session: AsyncSession, rules: Rules, party: Party, game_map: Map) -> tuple[FightRecord | None, list[str]]:
    """The roll for monsters at the end of a step, and the fight it starts. (None, []) if the party meets none."""
    if party.steps <= game_map.safe_steps:
        return None, []
    rate = game_map.tile(party.x, party.y)["encounter_rate"]
    fits = [each for each in game_map.zone(party.x, party.y)["encounters"] if len(each["monsters"]) <= rules.party_size]
    if not rate or not fits:
        return None, []
    world = await session.get(World, game_map.world_id)
    rolls = WorldRng(world.seed).stream("encounter", party.id, party.walked)
    if rolls.randrange(ENCOUNTER_SCALE) >= rate:
        return None, []
    monsters = rolls.choices(fits, weights=[each["weight"] for each in fits])[0]["monsters"]
    try:
        async with session.begin_nested():
            return await live.start_party_fight(session, party.id, monsters, rules), monsters
    except live.Refused:  # a party with no heroes, say: nothing to fight
        return None, []
