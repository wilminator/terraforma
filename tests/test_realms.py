"""Realms and settlements: found, fall, ruins and founding again, and a fallen town leaving every team's visited list."""

import pytest
from sqlalchemy import func, select

from terraforma.models import Map
from terraforma.realms import service
from terraforma.realms.models import DORMANT, LIVE, RUINS
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.towns.models import VisitedTown
from terraforma.world.start import ensure_start

from .test_towns import party_of_three

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


async def world_with_maps(db, *names):
    hub = await ensure_start(db)
    maps = []
    for name in names:
        game_map = Map(world_id=hub.world_id, name=name, title=name.title())
        db.add(game_map)
        maps.append(game_map)
    await db.flush()
    return hub.world_id, maps


async def test_a_realm_and_its_settlements_are_kept_by_key(db):
    world_id, (north, south) = await world_with_maps(db, "north", "south")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent", strength=10)
    await service.add_settlement(db, realm, north.id, "capital", "Ardent City", tier=3)
    await service.add_settlement(db, realm, south.id, "hamlet", "Hamlet", state=DORMANT)
    assert [each.key for each in await service.settlements(db, realm)] == ["capital", "hamlet"]
    assert [each.key for each in await service.settlements(db, realm, LIVE)] == ["capital"]
    await service.set_strength(db, realm, 4)
    assert realm.strength == 4


async def test_a_realm_or_settlement_key_can_not_be_used_twice(db):
    world_id, (north,) = await world_with_maps(db, "north")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    with pytest.raises(service.RealmError, match="already a realm"):
        await service.create_realm(db, world_id, "ardent", "Again")
    await service.add_settlement(db, realm, north.id, "capital", "Ardent City")
    with pytest.raises(service.RealmError, match="already a settlement"):
        await service.add_settlement(db, realm, north.id, "capital", "Again")


async def test_a_settlement_starts_live_or_dormant_on_a_map_that_exists(db):
    world_id, (north,) = await world_with_maps(db, "north")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    with pytest.raises(service.RealmError, match="live or dormant"):
        await service.add_settlement(db, realm, north.id, "a", "A", state=RUINS)
    with pytest.raises(service.RealmError, match="map does not exist"):
        await service.add_settlement(db, realm, 9999, "a", "A")
    with pytest.raises(service.RealmError, match="tier"):
        await service.add_settlement(db, realm, north.id, "a", "A", tier=-1)


async def test_a_fallen_settlement_is_kept_as_ruins_and_can_be_founded_again(db):
    world_id, (north,) = await world_with_maps(db, "north")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    village = await service.add_settlement(db, realm, north.id, "village", "Village")
    await service.fall(db, village)
    assert village.state == RUINS and not await service.live_on(db, north.id)
    with pytest.raises(service.RealmError, match="not live"):
        await service.fall(db, village)
    await service.found(db, village)
    assert village.state == LIVE and await service.live_on(db, north.id)
    with pytest.raises(service.RealmError, match="already live"):
        await service.found(db, village)


async def test_a_dormant_settlement_wakes_when_it_is_founded(db):
    world_id, (north,) = await world_with_maps(db, "north")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    village = await service.add_settlement(db, realm, north.id, "village", "Village", state=DORMANT)
    assert not await service.live_on(db, north.id)
    await service.found(db, village)
    assert await service.live_on(db, north.id)


async def test_a_town_that_falls_leaves_every_teams_visited_list(db):
    _, teams = await party_of_three(db)
    world_id, (north, south) = await world_with_maps(db, "north", "south")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    lost = await service.add_settlement(db, realm, north.id, "lost", "Lost")
    await service.add_settlement(db, realm, south.id, "kept", "Kept")
    await towns_service.record_visit(db, teams, north.id, 0, 0)
    await towns_service.record_visit(db, teams, south.id, 0, 0)
    await service.fall(db, lost)
    assert await db.scalar(select(func.count()).select_from(VisitedTown).where(VisitedTown.map_id == north.id)) == 0
    assert not await towns_service.has_visited(db, teams, north.id)
    assert await towns_service.has_visited(db, teams, south.id)
    assert [each["map"] for each in await towns_service.visited_towns(db, TownsEverywhere(), teams)] == ["south"]


async def test_a_map_stays_on_the_visited_list_while_another_settlement_on_it_is_live(db):
    _, teams = await party_of_three(db)
    world_id, (north,) = await world_with_maps(db, "north")
    realm = await service.create_realm(db, world_id, "ardent", "Ardent")
    first = await service.add_settlement(db, realm, north.id, "first", "First")
    await service.add_settlement(db, realm, north.id, "second", "Second")
    await towns_service.record_visit(db, teams, north.id, 0, 0)
    await service.fall(db, first)
    assert await towns_service.has_visited(db, teams, north.id)


class TownsEverywhere(Towns):
    async def is_town(self, session, map_id, x, y):
        return True
