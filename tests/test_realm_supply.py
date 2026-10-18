"""Settlement supplies: levels move with the world clock, events cut the sources, and the seed and admin calls reach them."""

import copy

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.models import advance_clock
from terraforma.realms import service, supply
from terraforma.realms.models import Material, Settlement, SettlementSupply
from terraforma.testing import in_app_db

from .helpers import expect
from .test_manual_quests import an_admin
from .test_realms import seed as realm_seed
from .test_realms import world_with_maps
from .test_towns import sign_in

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


def with_supply(**over):
    data = realm_seed()
    data["materials"] = [{"key": "iron", "name": "Iron"}, {"key": "hide", "name": "Hide"}]
    data["settlements"][0]["supplies"] = [{"material": "iron", "capacity": 50, "rate": 4, "drain": 1, "level": 10}, {"material": "hide", "capacity": 20}]
    data.update(over)
    return data


async def a_town(db):
    """A live settlement keeping iron: 10 of 50, 4 coming in and 1 used up each tick."""
    await load_content(db, with_supply())
    return await db.scalar(select(Settlement).where(Settlement.key == "capital"))


async def test_the_level_moves_with_the_world_clock_between_nothing_and_the_capacity(db):
    town = await a_town(db)
    assert (await supply.supply_of(db, town, "iron")).level == 10
    await advance_clock(db, town.world_id, 5)
    assert (await supply.supply_of(db, town, "iron")).level == 10 + 5 * 3
    await advance_clock(db, town.world_id, 100)
    assert (await supply.supply_of(db, town, "iron")).level == 50, "full"
    assert (await supply.supply_of(db, town, "hide")).level == 20, "a supply with no level starts full, and with no rate or drain holds"
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 1000)
    assert (await supply.supply_of(db, town, "iron")).level == 0, "nothing arrives and the use goes on"


async def test_an_event_cuts_the_sources_from_now_on_and_not_the_past(db):
    town = await a_town(db)
    await advance_clock(db, town.world_id, 4)  # 10 + 4 * 3 = 22
    row = await supply.set_cut(db, town, "iron", 50)
    assert row.level == 22 and row.cut == 50
    await advance_clock(db, town.world_id, 2)  # +4*50/100 - 1 = 1 a tick
    assert (await supply.supply_of(db, town, "iron")).level == 24
    await supply.set_cut(db, town, "iron", 0)
    await advance_clock(db, town.world_id, 1)
    assert (await supply.supply_of(db, town, "iron")).level == 27


async def test_a_crafter_takes_and_a_delivery_adds_within_what_there_is_and_what_fits(db):
    town = await a_town(db)
    assert await supply.take(db, town, "iron", 25) == 10
    assert (await supply.supply_of(db, town, "iron")).level == 0
    assert await supply.add(db, town, "iron", 80) == 50
    with pytest.raises(service.RealmError, match="negative"):
        await supply.take(db, town, "iron", -1)
    with pytest.raises(service.RealmError, match="negative"):
        await supply.add(db, town, "iron", -1)


async def test_a_cut_is_a_percent_and_a_supply_has_to_exist(db):
    town = await a_town(db)
    with pytest.raises(service.RealmError, match="percent"):
        await supply.set_cut(db, town, "iron", 101)
    with pytest.raises(service.RealmError, match="no such supply"):
        await supply.supply_of(db, town, "gold")
    assert [(material.key, row.level) for material, row in await supply.supplies(db, town)] == [("hide", 20), ("iron", 10)]


async def test_a_later_load_changes_the_numbers_but_not_the_level_or_the_cut(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 30)
    changed = with_supply()
    changed["settlements"][0]["supplies"][0].update(capacity=80, rate=9, level=1)
    await load_content(db, changed)
    row = await db.scalar(select(SettlementSupply).join(Material).where(Material.key == "iron").execution_options(populate_existing=True))
    assert (row.capacity, row.rate, row.level, row.cut) == (80, 9, 10, 30)


@pytest.mark.parametrize(("path", "value", "message"), [
    ("materials", [{"key": "iron", "name": "Iron"}], "doesn't have"),
    ("supplies", [{"material": "iron", "level": 5, "capacity": 4}], "level is above capacity"),
    ("supplies", [{"material": "iron"}, {"material": "iron"}], "twice"),
    ("supplies", [{"material": "iron", "rate": -1}], "rate"),
    ("supplies", [{"material": "iron", "surprise": 1}], "surprise"),
])
def test_a_bad_supply_is_refused_naming_the_problem(path, value, message):
    data = copy.deepcopy(with_supply())
    if path == "materials":
        data["materials"] = [{"key": "hide", "name": "Hide"}]
    else:
        data["settlements"][0]["supplies"] = value
    with pytest.raises(ContentError, match=message):
        check_seed(data)


def test_an_admin_reads_a_settlements_supply_and_cuts_a_source(app_client):
    root = an_admin(app_client)

    async def make(db):
        town = await a_town(db)
        await advance_clock(db, town.world_id, 2)
        return town.id

    town_id = in_app_db(app_client, make)
    listing = expect(app_client.get(f"/api/admin/settlements/{town_id}/supply"), 200).json()["supplies"]
    assert [(each["material"], each["level"], each["cut"]) for each in listing] == [("hide", 20, 0), ("iron", 16, 0)]
    cut = expect(app_client.post(f"/api/admin/settlements/{town_id}/supply/iron/cut", json={"cut": 40}, headers=root), 200).json()["supply"]
    assert (cut["material"], cut["level"], cut["cut"]) == ("iron", 16, 40)
    expect(app_client.post(f"/api/admin/settlements/{town_id}/supply/gold/cut", json={"cut": 40}, headers=root), 404)
    expect(app_client.post(f"/api/admin/settlements/999/supply/iron/cut", json={"cut": 40}, headers=root), 404)
    expect(app_client.get("/api/admin/settlements/999/supply"), 404)
    for bad in ({}, {"cut": 101}, {"cut": -1}, {"cut": "half"}, {"cut": 5, "surprise": 1}):
        expect(app_client.post(f"/api/admin/settlements/{town_id}/supply/iron/cut", json=bad, headers=root), 422)


def test_the_supply_calls_need_a_login_and_the_changing_one_a_csrf_token_and_an_admin(app_client):
    assert app_client.get("/api/admin/settlements/1/supply").status_code == 401
    mike = sign_in(app_client, "Mike")
    assert app_client.get("/api/admin/settlements/1/supply").status_code == 403
    assert app_client.post("/api/admin/settlements/1/supply/iron/cut", json={"cut": 1}).status_code == 403
    assert app_client.post("/api/admin/settlements/1/supply/iron/cut", json={"cut": 1}, headers=mike).status_code == 403
