"""The map calls: look at a map, and walk a party over it (see ``maps.walking``).

Each call is its own route with a strict model for its arguments. Walking changes something, so it needs a login and the CSRF
token (``ActingAccount``); looking only needs a login (``CurrentAccount``).
"""

from typing import Annotated, Self

from fastapi import APIRouter, HTTPException, Path, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules
from ..models import Map
from ..world.location import MapName
from . import walking

router = APIRouter(prefix="/api")

PartyId = Annotated[int, Path(ge=1)]
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
    of tile and their grid, and the kinds of zone with theirs. What a tile or zone meets or drops is kept from the page."""
    found = await db.scalar(select(Map).where(Map.name == name))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "there's no such map")
    return {
        "map": found.name, "title": found.title, "width": found.width, "height": found.height,
        "wrap_x": found.wrap_x, "wrap_y": found.wrap_y, "revision": found.revision,
        "tileset": [{key: kind[key] for key in ("name", "passable", "poison", "art")} for kind in found.tileset or []],
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
async def check_in(party_id: PartyId, body: TileRef, db: Db, account: ActingAccount, rules: GameRules) -> dict:
    """The page has reached a tile of the route. Answers whether the server confirms it (and any fight it ran into), or
    where the party really is when it doesn't (``confirmed`` false), so the page can go back there."""
    try:
        return await walking.step(db, rules, account.id, party_id, body.x, body.y)
    except walking.WalkError as error:
        raise refuse(error) from error
