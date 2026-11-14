"""A settlement that lives between realms (a wayhouse) is judged by the realms around it: it falls when none of them gives it enough and is founded
when one does."""

import copy

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.realms import strength, supply
from terraforma.realms.hooks import Realms
from terraforma.realms.models import LIVE, RUINS, Settlement

from .test_maps import SEED as MAPS_SEED

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


class Between(Realms):
    """The wayhouse 'way' is governed: its support is the best of the realms near it, how full their supplies are, less the way their nearest town is distant."""

    def governs(self, settlement):
        return settlement.key == "way"

    def support(self, settlement, influences):
        radius = settlement.sphere or self.sphere_default
        return max((each.fullness * (100 - 100 * each.nearest // radius) // 100 for each in influences), default=0)



class Meddling(Between):
    """A realm's own rule that would close and open the wayhouse at every pass: it must not be asked about a governed settlement."""

    def falls(self, realm, settlement):
        return settlement.key == "way"

    def founds(self, realm, settlement):
        return settlement.key == "way"


def seed(**way):
    data = copy.deepcopy(MAPS_SEED)
    data["maps"][0].update(tiles=[[0] * 70 for _ in range(20)])
    del data["maps"][0]["zone_tiles"]
    data["materials"] = [{"key": "grain", "name": "Grain"}]
    data["realms"] = [{"key": "north", "name": "North"}, {"key": "south", "name": "South"}, {"key": "free", "name": "Free", "kind": "minor"}]
    full = {"supplies": [{"material": "grain", "capacity": 100}]}
    data["settlements"] = [
        {"key": "near", "name": "Near", "realm": "north", "map": "meadow", "x": 10, "y": 10, **full},
        {"key": "far", "name": "Far", "realm": "south", "map": "meadow", "x": 60, "y": 10, **full},
        {"key": "way", "name": "Way", "realm": "free", "map": "meadow", "x": 14, "y": 12, "tier": 0, "sphere": 8, **way},
    ]
    return data


async def world(db, **way):
    await load_content(db, seed(**way))
    towns = {each.key: each for each in (await db.scalars(select(Settlement))).all()}
    return towns["way"], towns["near"], towns["far"]


async def test_the_sphere_holds_the_realms_with_a_live_town_inside_it_nearest_first(db):
    way, near, _far = await world(db)
    assert way.sphere == 8
    found = await strength.influences(db, Between(), way)
    assert [(each.realm.key, each.towns, each.nearest, each.fullness) for each in found] == [("north", 1, 4, 100)], "the far town is out of the sphere"
    near.state = RUINS
    await db.flush()
    assert await strength.influences(db, Between(), way) == [], "a town in ruins gives nothing"


async def test_a_settlement_with_no_sphere_of_its_own_uses_the_games_default(db):
    way, _near, _far = await world(db, sphere=None)
    assert way.sphere is None
    assert [each.realm.key for each in await strength.influences(db, Between(), way)] == ["north"]
    way.x = 40  # 20 from the far town, 30 from the near one: past the default 12
    assert await strength.influences(db, Between(), way) == []


async def test_a_governed_wayhouse_falls_when_nothing_around_it_gives_enough_and_is_founded_when_something_does(db):
    way, near, _far = await world(db)
    hooks = Between()
    assert await strength.judge(db, hooks) == 0 and way.state == LIVE, "support is 100 less the distance: 50"
    row = await supply.supply_of(db, near, "grain")
    row.level = 20  # 20% full: support 10, under holds_at (30)
    await db.flush()
    assert await strength.judge(db, hooks) == 1 and way.state == RUINS
    row.level = 100
    await db.flush()
    assert await strength.judge(db, hooks) == 0 and way.state == RUINS, "support 50 is over holds_at but not at founds_at (60): the gap keeps it closed"
    near.x, near.y = 12, 12  # the nearest town is 2 away: support 75
    await db.flush()
    assert await strength.judge(db, hooks) == 1 and way.state == LIVE
    row.level = 40  # 40 of 75 is 30: exactly holds_at, which holds
    await db.flush()
    assert await strength.judge(db, hooks) == 0 and way.state == LIVE
    row.level = 39
    await db.flush()
    assert await strength.judge(db, hooks) == 1 and way.state == RUINS


async def test_the_realms_own_rule_never_touches_a_governed_settlement(db):
    way, near, _far = await world(db)
    assert await strength.judge(db, Meddling()) == 0 and way.state == LIVE, "support is 50: the realm's falls() is not asked"
    (await supply.supply_of(db, near, "grain")).level = 0
    await db.flush()
    assert await strength.judge(db, Meddling()) == 1 and way.state == RUINS
    assert await strength.judge(db, Meddling()) == 0 and way.state == RUINS, "its realm's founds() is not asked either"


def test_a_sphere_is_a_positive_whole_number_in_the_seed():
    check_seed(seed())
    for bad in (0, -3, "wide"):
        with pytest.raises(ContentError):
            check_seed(seed(sphere=bad))
