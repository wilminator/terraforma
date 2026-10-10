"""A game's content: checked strictly, loaded repeatably, on every database."""

import copy
import json

import pytest
from sqlalchemy import select

from terraforma.content import models
from terraforma.content.loader import load_content
from terraforma.content.schema import STATS, ContentError, check_seed
from terraforma.game import Game
from terraforma.testing import in_app_db


SEED = {
    "abilities": [
        {"key": "fire", "name": "Fire", "kind": "spell", "mp_cost": 4,
         "effect": {"effect": "hurt", "targets": "group", "base": 10, "added": 2, "attribute": "fire"},
         "presentation": {"images": ["fx/fire.png"], "sounds": ["fx/fire.mp3"]}},
        {"key": "slash", "name": "Slash", "kind": "skill"},
    ],
    "items": [
        {"key": "potion", "name": "Potion", "price": 20, "one_use": True,
         "use_effect": {"effect": "heal", "base": 30}},
        {"key": "sword", "name": "Sword", "price": 100, "equip_slots": ["rhand"], "stat_bonus": {"Strength": 3},
         "attack": {"count": 1}},
    ],
    "personalities": [{"key": "plain", "name": "Plain", "hit": {"images": ["fx/hit.png"]}}],
    "jobs": [{"key": "fighter", "name": "Fighter", "xp_needed": 10, "stat_growth": {"HP": 5.5}, "abilities": ["slash"]}],
    "monsters": [
        {"key": "slime", "name": "Slime", "personality": "plain", "xp_reward": 3, "gold_reward": 1,
         "stats": {"HP": 8}, "abilities": ["slash"], "items": ["potion"], "equipment": ["sword"]},
    ],
}


def seed(**changes):
    data = copy.deepcopy(SEED)
    data.update(changes)
    return data


# --- checking ---------------------------------------------------------------------

def test_a_good_seed_checks_and_fills_in_defaults():
    checked = check_seed(SEED)
    slime = checked["monsters"][0]
    assert set(slime.stats) == set(STATS) and slime.stats["HP"] == 8 and slime.stats["Focus"] == 0
    assert checked["abilities"][1].effect.effect == "none"
    assert checked["items"][0].equip_slots is None


def test_files_that_are_not_content_are_left_alone():
    assert check_seed({"rumours": [{"anything": 1}]})["abilities"] == []


@pytest.mark.parametrize("kind, change, message", [
    ("abilities", {"kind": "magic"}, "kind"),
    ("abilities", {"surprise": 1}, "surprise"),
    ("abilities", {"mp_cost": "4"}, "mp_cost"),
    ("abilities", {"mp_cost": -1}, "mp_cost"),
    ("abilities", {"key": "Fire!"}, "key"),
    ("abilities", {"effect": {"effect": "explode"}}, "effect"),
    ("abilities", {"presentation": {"images": ["../secret.png"]}}, "images"),
    ("abilities", {"presentation": {"images": ["/etc/passwd"]}}, "images"),
    ("items", {"stat_bonus": {"Luck": 1}}, "stat_bonus"),
    ("jobs", {"stat_growth": {"HP": "lots"}}, "stat_growth"),
    ("monsters", {"ai": {"action": -1}}, "ai"),
])
def test_bad_rows_are_refused_naming_the_file_row_and_field(kind, change, message):
    data = seed()
    data[kind][0].update(change)
    with pytest.raises(ContentError) as error:
        check_seed(data)
    assert f"{kind}.json row 1" in str(error.value) and message in str(error.value)


def test_every_problem_is_reported_at_once():
    data = seed()
    data["abilities"][0]["kind"] = "magic"
    data["items"][0]["price"] = -1
    with pytest.raises(ContentError) as error:
        check_seed(data)
    assert "abilities.json" in str(error.value) and "items.json" in str(error.value)


def test_keys_are_unique_and_references_must_exist():
    data = seed()
    data["abilities"].append({"key": "fire", "name": "Fire again", "kind": "spell"})
    data["jobs"][0]["abilities"] = ["nope"]
    data["monsters"][0]["personality"] = "ghost"
    data["monsters"][0]["items"] = ["sword", "missing"]
    with pytest.raises(ContentError) as error:
        check_seed(data)
    text = str(error.value)
    assert "'fire' is used twice" in text
    assert "jobs.json fighter: abilities names 'nope'" in text
    assert "monsters.json slime: personality names 'ghost'" in text
    assert "items names 'missing'" in text and "'sword', " not in text


NPC = {"key": "keeper", "name": "Keeper", "map": "hub", "x": 1, "y": 1,
       "dialog": "Hello.`vend,potion,20,Bye`Well?`ack`Done.`jump,end``label,Bye`Bye."}


def test_an_npc_is_checked_like_the_rest():
    assert check_seed(seed(npcs=[NPC]))["npcs"][0].counter == []
    for change, message in (({"dialog": "`question,a`"}, "dialog"), ({"surprise": 1}, "surprise"), ({"x": -1}, "x"), ({"name": ""}, "name")):
        with pytest.raises(ContentError) as error:
            check_seed(seed(npcs=[{**NPC, **change}]))
        assert "npcs.json row 1 (keeper)" in str(error.value) and message in str(error.value)


def test_a_tile_kind_picks_the_frame_of_its_sheet_it_draws():
    def town(art):
        return {"key": "town", "name": "Town", "tileset": [{"art": art}], "tiles": [[0, 0], [0, 0]]}

    assert check_seed(seed(maps=[town({"sheet": "tiles", "frame": 5})]))["maps"][0].tileset[0].art.frame == 5
    assert check_seed(seed(maps=[town({"sheet": "tiles"})]))["maps"][0].tileset[0].art.frame == 0, "the first, unless it says"
    assert check_seed(seed(maps=[town("grass.svg")]))["maps"][0].tileset[0].art == "grass.svg", "a plain file is the one picture"
    for bad in (-1, "two", 1.5):
        with pytest.raises(ContentError, match="frame"):
            check_seed(seed(maps=[town({"sheet": "tiles", "frame": bad})]))


def test_an_npc_must_stand_on_a_map_the_game_has_and_inside_it():
    town = {"key": "town", "name": "Town", "tileset": [{}], "tiles": [[0, 0], [0, 0]]}
    with pytest.raises(ContentError) as error:
        check_seed(seed(npcs=[{**NPC, "map": "nowhere"}]))
    assert "npcs.json keeper: stands on 'nowhere', which maps.json doesn't have" in str(error.value)
    with pytest.raises(ContentError) as error:
        check_seed(seed(maps=[town], npcs=[{**NPC, "map": "town", "x": 5}]))
    assert "stands at (5, 1), which is off 'town'" in str(error.value)
    with pytest.raises(ContentError) as error:
        check_seed(seed(npcs=[{**NPC, "dialog": "Away.`warp,nowhere,0,0,end`"}]))
    assert "the dialog warps to 'nowhere'" in str(error.value)
    assert check_seed(seed(maps=[town], npcs=[{**NPC, "map": "town", "x": 1}]))["npcs"][0].map == "town"


# --- loading, on each database ----------------------------------------------------------

@pytest.mark.anyio
async def test_loading_stands_the_seeds_npcs_on_their_maps_and_loading_again_moves_them(db):
    from terraforma.npcs.models import Npc

    assert (await load_content(db, seed(npcs=[NPC])))["npcs"] == 1
    npc = (await db.scalars(select(Npc))).one()
    assert (npc.key, npc.name, npc.x, npc.y, npc.counter) == ("keeper", "Keeper", 1, 1, [])
    await load_content(db, seed(npcs=[{**NPC, "x": 2, "counter": [[2, 1]]}]))
    npc = (await db.scalars(select(Npc))).one()
    assert (npc.x, npc.counter) == (2, [[2, 1]]), "the NPC with that key was moved, not added"

async def rows(db, table):
    return (await db.scalars(select(table).order_by(table.key))).all()


@pytest.mark.anyio
async def test_loading_puts_every_kind_in_the_database(db):
    counts = await load_content(db, SEED)
    await db.commit()
    assert counts == {"abilities": 2, "items": 2, "personalities": 1, "jobs": 1, "monsters": 1}
    fire = (await rows(db, models.Ability))[0]
    assert fire.key == "fire" and fire.mp_cost == 4 and fire.effect["targets"] == "group" and fire.active
    assert fire.presentation["images"] == ["fx/fire.png"]
    plain = (await rows(db, models.Personality))[0]
    assert plain.animations["hit"]["images"] == ["fx/hit.png"] and plain.overworld["move"]["up"] == {"images": [], "times": []}
    slime = (await rows(db, models.Monster))[0]
    assert slime.stats["HP"] == 8 and slime.items == ["potion"] and slime.equipment == ["sword"]
    sword = [item for item in await rows(db, models.Item) if item.key == "sword"][0]
    assert sword.equip_slots == ["rhand"] and sword.use_effect is None and sword.one_use is False


@pytest.mark.anyio
async def test_loading_twice_changes_nothing_and_a_changed_row_is_updated_in_place(db):
    await load_content(db, SEED)
    first_id = (await rows(db, models.Ability))[0].id
    await load_content(db, SEED)
    assert len(await rows(db, models.Ability)) == 2
    data = seed()
    data["abilities"][0]["mp_cost"] = 9
    data["abilities"][0]["name"] = "Fireball"
    await load_content(db, data)
    fire = (await rows(db, models.Ability))[0]
    assert (fire.id, fire.mp_cost, fire.name) == (first_id, 9, "Fireball"), "same row, new values"


@pytest.mark.anyio
async def test_a_row_dropped_from_the_seed_is_kept_but_inactive_and_comes_back(db):
    await load_content(db, SEED)
    data = seed(items=[SEED["items"][0]], monsters=[], jobs=[])
    await load_content(db, data)
    items = {item.key: item for item in await rows(db, models.Item)}
    assert items["potion"].active and not items["sword"].active
    assert not (await rows(db, models.Monster))[0].active and not (await rows(db, models.Job))[0].active
    await load_content(db, SEED)
    assert all(item.active for item in await rows(db, models.Item))


@pytest.mark.anyio
async def test_a_file_missing_from_the_seed_is_left_alone(db):
    await load_content(db, SEED)
    await load_content(db, {"abilities": SEED["abilities"]})
    assert all(item.active for item in await rows(db, models.Item)), "no items.json: items untouched"


@pytest.mark.anyio
async def test_a_bad_seed_loads_nothing(db):
    data = seed()
    data["monsters"][0]["personality"] = "ghost"
    with pytest.raises(ContentError):
        await load_content(db, data)
    assert await rows(db, models.Ability) == []


# --- starting the app --------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_the_app_loads_the_games_seed_on_start(app_client):
    async def count(db):
        return len(await rows(db, models.Monster)), len(await rows(db, models.Ability))

    assert in_app_db(app_client, count) == (1, 2)


def test_the_app_refuses_to_start_on_a_bad_seed(database_url, mailbox, tmp_path, game):
    from starlette.testclient import TestClient

    from terraforma.app import create_app
    from terraforma.db.session import make_engine
    from terraforma.keys import new_key
    from terraforma.settings import Settings
    from terraforma.testing import SECRET, fresh_database, run

    async def prepare():
        engine = make_engine(database_url)
        await fresh_database(engine, database_url)
        await engine.dispose()

    run(prepare())
    new_key(tmp_path / "keys")
    (game.seed_dir / "jobs.json").write_text(json.dumps([{"key": "x", "name": "X", "abilities": ["nope"]}]))
    settings = Settings(database_url=database_url, session_secret=SECRET, key_dir=tmp_path / "keys")
    with pytest.raises(ContentError, match="nope"):
        with TestClient(create_app(settings, game, mailer=mailbox)):
            pass

