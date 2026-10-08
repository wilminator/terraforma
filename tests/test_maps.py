"""Maps: the seed format is checked strictly, loading is repeatable and bumps the revision, and a map answers about its tiles and zones."""

import copy
import json

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.game import Game
from terraforma.models import Map
from terraforma.pvp.hooks import PvpZones
from terraforma.testing import in_app_db

SEED = {
    "items": [{"key": "potion", "name": "Potion"}],
    "personalities": [{"key": "plain", "name": "Plain"}],
    "monsters": [{"key": "slime", "name": "Slime", "personality": "plain"}],
    "drop_tables": [{"key": "herbs", "name": "Herbs", "entries": [{"item": "potion", "chance": 5000}]}],
    "maps": [{
        "key": "meadow", "name": "The Meadow", "wrap_x": True, "safe_steps": 3,
        "tileset": [{"name": "grass", "encounter_rate": 500, "art": "tiles/grass.png"}, {"name": "rock", "passable": False}, {"name": "bog", "poison": True}],
        "tiles": [[0, 0, 1],
                  [0, 2, 1]],
        "zones": [{"name": "safe"}, {"name": "wilds", "encounters": [{"monsters": ["slime", "slime"], "weight": 3}], "drops": ["herbs"], "pvp": True}],
        "zone_tiles": [[0, 1, 1],
                       [0, 1, 1]],
    }],
}


def seed(**changes):
    data = copy.deepcopy(SEED)
    data["maps"][0].update(changes)
    return data


# --- checking ---------------------------------------------------------------------

def test_a_good_map_checks_and_fills_in_defaults():
    meadow = check_seed(SEED)["maps"][0]
    assert (meadow.width, meadow.height) == (3, 2)
    assert meadow.tileset[0].passable and not meadow.tileset[1].passable and meadow.tileset[2].poison
    assert meadow.zones[1].encounters[0].weight == 3 and meadow.zones[1].pvp and not meadow.zones[0].pvp


def test_a_map_can_be_nothing_but_a_tileset_and_a_grid():
    plain = check_seed({"maps": [{"key": "hall", "name": "Hall", "tileset": [{}], "tiles": [[0]]}]})["maps"][0]
    assert plain.zone_tiles is None and len(plain.zones) == 1 and not plain.wrap_x and not plain.wrap_y and plain.safe_steps == 0


@pytest.mark.parametrize("changes, message", [
    ({"tiles": [[0, 0], [0]]}, "same number of tiles"),
    ({"tiles": []}, "tiles"),
    ({"tiles": [[]]}, "same number of tiles"),
    ({"tiles": [[0, 3, 0]]}, "tileset"),
    ({"tiles": [[0, -1, 0]]}, "tileset"),
    ({"tiles": [[0] * 513]}, "same number of tiles"),
    ({"zone_tiles": [[0, 1, 1]]}, "same size"),
    ({"zone_tiles": [[0, 2, 1], [0, 1, 1]]}, "zones"),
    ({"tileset": []}, "tileset"),
    ({"tileset": [{"encounter_rate": 10001}, {}, {}]}, "encounter_rate"),
    ({"tileset": [{"art": "../x.png"}, {}, {}]}, "art"),
    ({"tileset": [{"art": {"sheet": "../x"}}, {}, {}]}, "art"),
    ({"tileset": [{"art": {"colors": "red"}}, {}, {}]}, "sheet"),
    ({"tileset": [{"art": {"sheet": "knight", "palette": "red"}}, {}, {}]}, "palette"),
    ({"tileset": [{"art": {"sheet": "knight", "colors": "/etc/x"}}, {}, {}]}, "colors"),
    ({"tileset": [{"surprise": 1}, {}, {}]}, "surprise"),
    ({"wrap_x": "yes"}, "wrap_x"),
    ({"safe_steps": -1}, "safe_steps"),
    ({"key": "Meadow!"}, "key"),
    ({"zones": []}, "zones"),
    ({"zones": [{"encounters": [{"monsters": []}]}] * 2}, "monsters"),
    ({"zones": [{"encounters": [{"monsters": ["slime"], "weight": 0}]}] * 2}, "weight"),
    ({"wraps": True}, "wraps"),
])
def test_a_bad_map_is_refused_naming_the_problem(changes, message):
    with pytest.raises(ContentError, match=message):
        check_seed(seed(**changes))


def test_a_zone_can_only_name_monsters_and_drops_that_exist():
    data = seed(zones=[{"encounters": [{"monsters": ["dragon"]}], "drops": ["gold"]}] * 2)
    with pytest.raises(ContentError) as error:
        check_seed(data)
    assert "'dragon', which monsters.json doesn't have" in str(error.value)
    assert "'gold', which drop_tables.json doesn't have" in str(error.value)


def test_a_map_key_can_not_be_used_twice():
    data = copy.deepcopy(SEED)
    data["maps"].append(data["maps"][0])
    with pytest.raises(ContentError, match="used twice"):
        check_seed(data)


# --- the map itself ---------------------------------------------------------------

def test_a_map_wraps_only_the_directions_that_wrap():
    flat = Map(name="a", width=3, height=2)
    assert flat.normalize(1, 1) == (1, 1) and flat.normalize(3, 0) is None and flat.normalize(0, -1) is None
    tube = Map(name="b", width=3, height=2, wrap_x=True)
    assert tube.normalize(3, 1) == (0, 1) and tube.normalize(-1, 0) == (2, 0) and tube.normalize(0, 2) is None
    globe = Map(name="c", width=3, height=2, wrap_x=True, wrap_y=True)
    assert globe.normalize(-1, -1) == (2, 1) and globe.normalize(7, 5) == (1, 1)


def test_a_map_without_a_grid_is_open_ground_in_a_plain_zone():
    hub = Map(name="hub", width=4, height=4)
    assert hub.tile(2, 2)["passable"] and hub.tile(2, 2)["encounter_rate"] == 0
    assert hub.zone(2, 2) == {"name": "", "encounters": [], "drops": [], "pvp": False, "can_flee": True}


# --- loading ----------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_tiles_art_can_be_a_sheet_with_a_color_map(db):
    art = {"sheet": "tiles/knight", "colors": "tiles/red_team", "animations": None}
    seed = {**SEED, "maps": [{**SEED["maps"][0], "tileset": [{"name": "grass", "art": {"sheet": "tiles/knight", "colors": "tiles/red_team"}}, {}, {}]}]}
    await load_content(db, seed)
    await db.commit()
    assert (await db.scalar(select(Map).where(Map.name == "meadow"))).tile(0, 0)["art"] == art


@pytest.mark.anyio
async def test_loading_puts_the_map_in_the_engines_world(db):
    counts = await load_content(db, SEED)
    await db.commit()
    assert counts["maps"] == 1
    meadow = await db.scalar(select(Map).where(Map.name == "meadow"))
    assert (meadow.title, meadow.width, meadow.height, meadow.wrap_x, meadow.wrap_y, meadow.safe_steps, meadow.revision) == ("The Meadow", 3, 2, True, False, 3, 1)
    assert meadow.tiles == [[0, 0, 1], [0, 2, 1]] and meadow.zone_tiles == [[0, 1, 1], [0, 1, 1]]
    assert meadow.tile(0, 0) == {"name": "grass", "passable": True, "poison": False, "encounter_rate": 500, "art": "tiles/grass.png"}
    assert meadow.tile(1, 1)["poison"] and not meadow.tile(2, 0)["passable"]
    assert meadow.tile(3, 0)["name"] == "grass", "x wraps"
    assert meadow.zone(0, 0)["name"] == "safe" and meadow.zone(1, 0)["pvp"]
    assert meadow.zone(1, 0)["encounters"] == [{"monsters": ["slime", "slime"], "weight": 3}] and meadow.zone(1, 0)["drops"] == ["herbs"]
    assert meadow.zone(0, 2)["name"] == "" and meadow.tile(0, 2)["passable"], "off the map (y does not wrap) is nothing"


@pytest.mark.anyio
async def test_loading_with_no_zone_grid_makes_the_whole_map_the_first_zone(db):
    await load_content(db, {"maps": [{"key": "hall", "name": "Hall", "tileset": [{}], "tiles": [[0, 0], [0, 0]], "zones": [{"name": "all", "pvp": True}]}]})
    await db.commit()
    hall = await db.scalar(select(Map).where(Map.name == "hall"))
    assert hall.zone_tiles == [[0, 0], [0, 0]] and hall.zone(1, 1) == {"name": "all", "encounters": [], "drops": [], "pvp": True, "can_flee": True}


@pytest.mark.anyio
async def test_loading_again_leaves_the_revision_and_a_change_raises_it(db):
    await load_content(db, SEED)
    await load_content(db, SEED)
    await db.commit()
    assert (await db.scalar(select(Map).where(Map.name == "meadow"))).revision == 1
    await load_content(db, seed(tiles=[[0, 0, 1], [0, 0, 1]]))
    await db.commit()
    meadow = await db.scalar(select(Map).where(Map.name == "meadow"))
    assert meadow.revision == 2 and meadow.tiles == [[0, 0, 1], [0, 0, 1]]
    await load_content(db, seed(safe_steps=0, wrap_y=True, tiles=[[0, 0, 1], [0, 0, 1]]))
    await db.commit()
    meadow = await db.scalar(select(Map).where(Map.name == "meadow"))
    assert meadow.revision == 3 and meadow.safe_steps == 0 and meadow.wrap_y


@pytest.mark.anyio
async def test_a_map_the_seed_drops_stays_and_the_hub_can_be_drawn(db):
    await load_content(db, SEED)
    await load_content(db, {"maps": [{"key": "hub", "name": "Hub", "tileset": [{}], "tiles": [[0] * 5] * 4}]})
    await load_content(db, {"maps": []})
    await db.commit()
    maps = {found.name: found for found in (await db.scalars(select(Map))).all()}
    assert set(maps) == {"hub", "meadow"}
    assert (maps["hub"].width, maps["hub"].height, maps["hub"].title, maps["hub"].revision) == (5, 4, "Hub", 1)


@pytest.mark.anyio
async def test_a_bad_map_loads_nothing(db):
    with pytest.raises(ContentError):
        await load_content(db, seed(tiles=[[9]]))
    assert (await db.scalars(select(Map))).all() == []


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_the_app_loads_the_games_maps_on_start(app_client):
    async def read(db):
        return (await db.scalar(select(Map).where(Map.name == "meadow"))).revision

    assert in_app_db(app_client, read) == 1


def test_the_app_keeps_the_revision_when_it_starts_again(app_client, mailbox, game):
    from starlette.testclient import TestClient

    from terraforma.app import create_app

    async def read(db):
        return (await db.scalar(select(Map).where(Map.name == "meadow"))).revision

    with TestClient(create_app(app_client.app.state.settings, game, mailer=mailbox)) as second:
        assert in_app_db(second, read) == 1


# --- PvP by zone ------------------------------------------------------------------

@pytest.mark.anyio
async def test_the_default_pvp_rule_follows_the_zone_flag(db):
    await load_content(db, SEED)
    await db.commit()
    meadow = await db.scalar(select(Map).where(Map.name == "meadow"))
    zones = PvpZones()
    assert await zones.allows_pvp(db, meadow.id, 1, 0) is True
    assert await zones.allows_pvp(db, meadow.id, 0, 0) is False
    assert await zones.allows_pvp(db, 99999, 0, 0) is False, "no such map"
