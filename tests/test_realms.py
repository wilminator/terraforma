"""Realms and settlements: found, fall, ruins and founding again, and a fallen town leaving every team's visited list."""

import copy

import pytest
from sqlalchemy import func, select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.models import Map
from terraforma.realms import service
from terraforma.realms.models import DORMANT, LIVE, RUINS, Realm, Settlement
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.towns.models import VisitedTown
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_manual_quests import an_admin
from .test_maps import SEED as MAPS_SEED
from .test_towns import party_of_three, sign_in

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


# --- the seed ---------------------------------------------------------------------------------------------------------

def seed(**changes):
    data = copy.deepcopy(MAPS_SEED)
    data["realms"] = [{"key": "ardent", "name": "Ardent", "strength": 7}]
    data["settlements"] = [{"key": "capital", "name": "Ardent City", "realm": "ardent", "map": "meadow", "tier": 3},
                           {"key": "hamlet", "name": "Hamlet", "realm": "ardent", "map": "hub", "state": "dormant"}]
    data.update(changes)
    return data


async def test_the_seed_makes_realms_and_settlements_and_a_later_load_leaves_what_the_world_moved(db):
    await load_content(db, seed())
    realm = await db.scalar(select(Realm).where(Realm.key == "ardent"))
    capital = await db.scalar(select(Settlement).where(Settlement.key == "capital"))
    assert (realm.name, realm.strength, capital.state, capital.tier) == ("Ardent", 7, LIVE, 3)
    assert (await db.scalar(select(Settlement.state).where(Settlement.key == "hamlet"))) == DORMANT
    await service.set_strength(db, realm, 2)
    await service.fall(db, capital)
    changed = seed()
    changed["realms"][0].update(name="Ardent Realm", strength=99)
    changed["settlements"][0].update(name="Capital", tier=4, state="dormant")
    assert (await load_content(db, changed))["realms"] == 3
    realm = await db.scalar(select(Realm).where(Realm.key == "ardent").execution_options(populate_existing=True))
    capital = await db.scalar(select(Settlement).where(Settlement.key == "capital").execution_options(populate_existing=True))
    assert (realm.name, realm.strength) == ("Ardent Realm", 2), "the strength is the world's now"
    assert (capital.name, capital.tier, capital.state) == ("Capital", 4, RUINS), "the state is the world's now"


@pytest.mark.parametrize(("changes", "message"), [
    ({"realms": [{"key": "ardent", "name": "Ardent", "surprise": 1}]}, "surprise"),
    ({"settlements": [{"key": "a", "name": "A", "realm": "nowhere", "map": "meadow"}]}, "realms.json doesn't have"),
    ({"settlements": [{"key": "a", "name": "A", "realm": "ardent", "map": "nowhere"}]}, "maps.json doesn't have"),
    ({"settlements": [{"key": "a", "name": "A", "realm": "ardent", "map": "meadow", "state": "ruins"}]}, "state"),
    ({"settlements": [{"key": "a", "name": "A", "realm": "ardent", "map": "meadow", "tier": -1}]}, "tier"),
    ({"settlements": [{"key": "a", "name": "A", "realm": "ardent", "map": "meadow"}] * 2}, "used twice"),
])
def test_a_bad_realm_or_settlement_is_refused_naming_the_problem(changes, message):
    with pytest.raises(ContentError, match=message):
        check_seed(seed(**changes))


# --- the admin's calls ------------------------------------------------------------------------------------------------

def test_the_calls_need_a_login_and_the_changing_ones_a_csrf_token_and_an_admin(app_client):
    assert app_client.get("/api/admin/realms").status_code == 401
    mike = sign_in(app_client, "Mike")
    assert app_client.get("/api/admin/realms").status_code == 403, "not an admin"
    assert app_client.post("/api/admin/realms/1/strength", json={"strength": 1}).status_code == 403, "no token"
    for path, body in (("/realms/1/strength", {"strength": 1}), ("/settlements/1/fall", None), ("/settlements/1/found", None)):
        assert app_client.post(f"/api/admin{path}", json=body, headers=mike).status_code == 403, "not an admin"


def test_an_admin_reads_the_realms_moves_a_strength_and_lets_a_town_fall_and_rise(app_client):
    root = an_admin(app_client)

    async def make(db):
        world_id, (north,) = await world_with_maps(db, "north")
        realm = await service.create_realm(db, world_id, "ardent", "Ardent", strength=5)
        town = await service.add_settlement(db, realm, north.id, "village", "Village")
        return realm.id, town.id

    realm_id, town_id = in_app_db(app_client, make)
    listing = expect(app_client.get("/api/admin/realms"), 200).json()
    assert [(each["key"], each["strength"]) for each in listing["realms"]] == [("ardent", 5)]
    assert [(each["key"], each["state"]) for each in listing["settlements"]] == [("village", LIVE)]
    assert expect(app_client.post(f"/api/admin/realms/{realm_id}/strength", json={"strength": -3}, headers=root), 200).json()["realm"]["strength"] == -3
    assert expect(app_client.post(f"/api/admin/settlements/{town_id}/fall", headers=root), 200).json()["settlement"]["state"] == RUINS
    expect(app_client.post(f"/api/admin/settlements/{town_id}/fall", headers=root), 422)
    assert expect(app_client.post(f"/api/admin/settlements/{town_id}/found", headers=root), 200).json()["settlement"]["state"] == LIVE
    expect(app_client.post(f"/api/admin/settlements/{town_id}/found", headers=root), 422)
    expect(app_client.post("/api/admin/settlements/999/fall", headers=root), 404)
    expect(app_client.post("/api/admin/realms/999/strength", json={"strength": 1}, headers=root), 404)
    for bad in ({}, {"strength": "a lot"}, {"strength": 2_000_000}, {"strength": 1, "surprise": 1}):
        expect(app_client.post(f"/api/admin/realms/{realm_id}/strength", json=bad, headers=root), 422)


async def test_a_minor_power_is_a_realm_that_holds_settlements_under_no_kingdom(db):
    data = seed()
    data["realms"].append({"key": "free", "name": "Free Towns", "strength": 2, "kind": "minor"})
    data["settlements"].append({"key": "port", "name": "Free Port", "realm": "free", "map": "meadow"})
    await load_content(db, data)
    kinds = {each.key: each.kind for each in (await db.scalars(select(Realm))).all()}
    assert kinds == {"ardent": "kingdom", "free": "minor"}, "a kingdom unless the seed says minor"
    port = await db.scalar(select(Settlement).where(Settlement.key == "port"))
    assert (await service.fall(db, port)).state == RUINS, "it falls and is founded like any other"
    assert (await service.found(db, port)).state == LIVE
    assert service.realm_view(await db.scalar(select(Realm).where(Realm.key == "free")))["kind"] == "minor"


async def test_a_realm_is_a_kingdom_or_a_minor_power_and_nothing_else(db):
    data = seed()
    data["realms"][0]["kind"] = "empire"
    with pytest.raises(ContentError):
        check_seed(data)
    world_id, _ = await world_with_maps(db, "north")
    with pytest.raises(service.RealmError):
        await service.create_realm(db, world_id, "odd", "Odd", kind="empire")
