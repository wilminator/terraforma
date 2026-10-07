"""The map calls: look at a map, and walk a party over it (see ``maps.walking``).

Each call is its own route with a strict model for its arguments. Walking changes something, so it needs a login and the CSRF
token (``ActingAccount``); looking only needs a login (``CurrentAccount``).
"""

from functools import partial
from typing import Annotated, Self

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..api.deps import ActingAccount, CurrentAccount, Db, GameEconomy, GameInn, GameNpcs, GameReach, GameRules, GameTowns
from ..heroes import service as heroes
from ..heroes.routes import Id as HeroId
from ..heroes.routes import refuse as refuse_hero
from ..models import Map
from ..npcs import inn as inns
from ..npcs import service as npcs
from ..towns import service as towns
from ..world.location import MapName
from . import objects, walking
from .models import EDGE, MapObject

router = APIRouter(prefix="/api")

PartyId = Annotated[int, Path(ge=1)]
ObjectId = Annotated[int, Path(ge=1)]
Coordinate = Annotated[int, Field(ge=-1_000_000, le=1_000_000)]


class Strict(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class TileRef(Strict):
    x: Coordinate
    y: Coordinate


class RouteRequest(Strict):
    """Where to walk: tiles to pass through in order (the server fills the gaps between them), or a destination."""

    path: list[TileRef] | None = Field(default=None, min_length=1, max_length=512)
    destination: TileRef | None = None

    @model_validator(mode="after")
    def one_of(self) -> Self:
        if (self.path is None) == (self.destination is None):
            raise ValueError("give either a path or a destination")
        return self

    def waypoints(self) -> list[tuple[int, int]]:
        tiles = self.path if self.path is not None else [self.destination]
        return [(tile.x, tile.y) for tile in tiles]


def refuse(error: walking.WalkError) -> HTTPException:
    code = {walking.NotFound: status.HTTP_404_NOT_FOUND, walking.NotYours: status.HTTP_403_FORBIDDEN}.get(type(error), status.HTTP_409_CONFLICT)
    return HTTPException(code, str(error))


@router.get("/maps/{name}")
async def look_at_map(name: Annotated[MapName, Path()], db: Db, account: CurrentAccount) -> dict:
    """A map as a player's page draws it: its size, wrap flags, revision (a page holding an older one asks again), the kinds
    of tile and their grid, and the kinds of zone with theirs. What a tile or zone meets or drops is kept from the page, and so is what an object's or an edge's script says. ``objects`` are the things a hero can use
    (``key``, ``name``, ``kind``, ``action``, tile) and ``edges`` the edges that have an event, where a route may leave the map."""
    found = await db.scalar(select(Map).where(Map.name == name))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such map")
    rows = (await db.scalars(select(MapObject).where(MapObject.map_id == found.id).order_by(MapObject.id))).all()
    things, edges = [each for each in rows if each.kind != EDGE], [each for each in rows if each.kind == EDGE]
    return {
        "map": found.name, "title": found.title, "width": found.width, "height": found.height,
        "wrap_x": found.wrap_x, "wrap_y": found.wrap_y, "revision": found.revision,
        "tileset": [{key: kind[key] for key in ("name", "passable", "poison", "art")} for kind in found.tileset or []],
        "objects": [{"id": each.id, "key": each.key, "name": each.name, "kind": each.kind, "action": each.action, "x": each.x, "y": each.y} for each in things],
        "edges": sorted(each.edge for each in edges),
        "tiles": found.tiles, "zones": [{key: kind[key] for key in ("name", "pvp")} for kind in found.zones or []], "zone_tiles": found.zone_tiles,
    }


@router.get("/parties/{party_id}/route")
async def where_is_the_party(party_id: PartyId, db: Db, account: CurrentAccount) -> dict:
    """Where the party stands and the route it is on, if any (a page that reloads picks its walk up from this)."""
    try:
        return await walking.where(db, account.id, party_id)
    except walking.WalkError as error:
        raise refuse(error) from error


@router.post("/parties/{party_id}/route")
async def make_route(party_id: PartyId, body: RouteRequest, db: Db, account: ActingAccount, rules: GameRules) -> dict:
    """Proposes a route for the party (its leader only): the server answers with the route it will confirm (the party's own
    tile first), gaps filled in, or refuses (409) a tile that is off the map or can't be walked on, or no way there."""
    try:
        return await walking.plan(db, rules, account.id, party_id, body.waypoints())
    except walking.WalkError as error:
        raise refuse(error) from error


@router.post("/parties/{party_id}/route/step")
async def check_in(party_id: PartyId, body: TileRef, db: Db, account: ActingAccount, rules: GameRules, hooks: GameNpcs, inn: GameInn, economy: GameEconomy, town: GameTowns) -> dict:
    """The page has reached a tile of the route. Answers whether the server confirms it (and any fight it ran into), or
    where the party really is when it doesn't (``confirmed`` false), so the page can go back there. A step off an edge of the
    map that has an event leaves the party where it stands and answers with ``edge`` (its direction) and ``dialog``, the
    first step of the event's script (a conversation frame; ``window`` says whether to open the dialog window). A party that walks
    into a town is suspended there (the game's ``Towns.is_town``): the answer says ``town`` and the route is over."""
    try:
        async def run(hero, edge: MapObject) -> dict:
            return await npcs.start(db, hooks, hero, edge, partial(inns.rest, db, inn, rules, economy), economy)

        return await walking.step(db, rules, account.id, party_id, body.x, body.y, run, town)
    except walking.WalkError as error:
        raise refuse(error) from error


@router.post("/heroes/{hero_id}/objects/{object_id}/use")
async def use_object(hero_id: HeroId, object_id: ObjectId, db: Db, account: ActingAccount, hooks: GameNpcs, reach: GameReach, inn: GameInn, rules: GameRules, economy: GameEconomy, town: GameTowns) -> dict:
    """The hero uses a chest, door or the like in reach of them (the game's ``Reach.map_object``, for the object's own action;
    the nearby list for that action shows what is). Answers with the first step of its script: ``events`` to show, the
    ``prompt`` if it asks something, and ``window``: whether the page should open its dialog window. Refused (409) out of reach
    or in a fight, and 404 for a thing that isn't there."""
    try:
        hero = await heroes.own_hero(db, account, hero_id)
        frame = await objects.use(db, hooks, reach, hero, object_id, partial(inns.rest, db, inn, rules, economy), economy)
        await towns.settle_hero(db, town, hero.id)  # (a door may have warped the party into a town)
        return frame
    except objects.NoSuchObject as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(error)) from error
    except npcs.NpcError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    except heroes.HeroError as error:
        raise refuse_hero(error) from error
