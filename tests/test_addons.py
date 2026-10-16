"""Add-ons (slice 1): the settings list them, their seed and assets are layered under the game's, and clashes and unlicensed assets stop the server."""

import json

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from starlette.testclient import TestClient

from terraforma.addons import AddOn, AddOnError, integrate, layer_seeds, license_problem
from terraforma.app import create_app
from terraforma.content.models import Item
from terraforma.example import GAME as EXAMPLE
from terraforma.game import Game
from terraforma.settings import load_settings

from .helpers import expect
from .test_settings import write

pytestmark = pytest.mark.platform


HERB = {"key": "herb", "name": "Herb", "price": 12, "description": "A bitter leaf.", "one_use": True,
        "use_effect": {"effect": "heal", "targets": "individual", "base": 10, "added": 0}}
TONIC = {**HERB, "key": "tonic", "name": "Tonic"}

RECORD = "| File | Source | License |\n|---|---|---|\n| {files} | {source} | {license} |\n"


def make_seed(folder, **files):
    folder.mkdir(parents=True, exist_ok=True)
    for name, rows in files.items():
        (folder / f"{name}.json").write_text(json.dumps(rows))
    return folder


def make_assets(folder, names=("one.svg",), record="MIT", source="Drawn for this test"):
    """An assets folder with $names, recorded (unless $record is None) as one row."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).parent.mkdir(parents=True, exist_ok=True)
        (folder / name).write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    if record is not None:
        files = ", ".join(f"`{name}`" for name in names)
        (folder / "ASSETS.md").write_text(RECORD.format(files=files, source=source, license=record))
    return folder


# --- the settings ------------------------------------------------------------------------------------------------

def test_the_settings_list_addons_by_key(tmp_path):
    secret = 'session_secret = "' + "x" * 32 + '"\n'
    assert load_settings(write(tmp_path, secret)).addons == []
    assert load_settings(write(tmp_path, secret + 'addons = ["monster_pack", "dungeons"]\n')).addons == ["monster_pack", "dungeons"]


@pytest.mark.parametrize("listed", ['["Monster Pack"]', '["x"]', '["1pack"]', '["pack", "pack"]', '["../pack"]'])
def test_a_bad_or_repeated_addon_key_in_the_settings_is_refused(tmp_path, listed):
    with pytest.raises(ValidationError):
        load_settings(write(tmp_path, 'session_secret = "' + "x" * 32 + f'"\naddons = {listed}\n'))


# --- which add-ons the game supports ------------------------------------------------------------------------------

def test_a_game_without_the_addon_refuses_to_use_it(tmp_path):
    game = Game(name="G", addons=(AddOn(key="pack", name="Pack"),))
    assert integrate(game, []).addons == () and integrate(game, ["pack"]).addons == (game.addons[0],)
    with pytest.raises(AddOnError, match="'other' is not one this game supports"):
        integrate(game, ["other"])
    with pytest.raises(AddOnError, match="supports: none"):
        integrate(Game(name="G"), ["pack"])
    with pytest.raises(AddOnError, match="no game|not one this game supports"):
        integrate(None, ["pack"])
    with pytest.raises(AddOnError, match="listed twice"):
        integrate(game, ["pack", "pack"])


def test_the_games_own_addon_list_is_checked(tmp_path):
    with pytest.raises(AddOnError, match="the key must be"):
        integrate(Game(name="G", addons=(AddOn(key="Bad Key", name="x"),)), [])
    twice = (AddOn(key="pack", name="One"), AddOn(key="pack", name="Two"))
    with pytest.raises(AddOnError, match="lists the add-on 'pack' twice"):
        integrate(Game(name="G", addons=twice), [])
    with pytest.raises(AddOnError, match="seed_dir .* is not a folder"):
        integrate(Game(name="G", addons=(AddOn(key="pack", name="x", seed_dir=tmp_path / "nothing"),)), ["pack"])


def test_an_addon_nobody_lists_is_not_looked_at(tmp_path):
    game = Game(name="G", addons=(AddOn(key="pack", name="x", seed_dir=tmp_path / "nothing", assets_dir=tmp_path / "nothing"),))
    assert integrate(game, []).addons == ()


# --- the seed ----------------------------------------------------------------------------------------------------

def test_seeds_are_layered_the_games_first_then_each_addon_in_the_order_listed(tmp_path):
    base = make_seed(tmp_path / "base", items=[HERB], monsters=[{"key": "rat"}])
    one = make_seed(tmp_path / "one", items=[TONIC], jobs=[{"key": "thief"}])
    two = make_seed(tmp_path / "two", items=[{**HERB, "key": "elixir"}])
    layered = layer_seeds(((None, base), ("one", one), ("two", two)))
    assert [row["key"] for row in layered.rows["items"]] == ["herb", "tonic", "elixir"]
    assert layered.rows["jobs"] == [{"key": "thief"}] and layered.rows["monsters"] == [{"key": "rat"}]
    assert layered.owners["items"] == {"herb": None, "tonic": "one", "elixir": "two"}
    assert layered.owners["jobs"] == {"thief": "one"}


def test_a_key_in_two_layers_is_refused_naming_both(tmp_path):
    base = make_seed(tmp_path / "base", items=[HERB])
    one = make_seed(tmp_path / "one", items=[TONIC])
    two = make_seed(tmp_path / "two", items=[TONIC])
    with pytest.raises(AddOnError, match=r"items.json: the key 'herb' is in the game and in the add-on one"):
        layer_seeds(((None, base), ("one", make_seed(tmp_path / "again", items=[HERB]))))
    with pytest.raises(AddOnError, match=r"items.json: the key 'tonic' is in the add-on one and in the add-on two"):
        layer_seeds(((None, base), ("one", one), ("two", two)))


def test_a_seed_file_that_is_not_json_names_its_owner(tmp_path):
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "items.json").write_text("{")
    with pytest.raises(AddOnError, match="one's seed: items.json: not valid JSON"):
        layer_seeds((("one", bad),))


def test_rows_without_a_key_are_joined_without_a_clash(tmp_path):
    layered = layer_seeds((("a", make_seed(tmp_path / "a", other=[{"n": 1}])), ("b", make_seed(tmp_path / "b", other=[{"n": 1}]))))
    assert layered.rows["other"] == [{"n": 1}, {"n": 1}]


# --- the assets --------------------------------------------------------------------------------------------------

def test_asset_folders_are_layered_the_games_first(tmp_path):
    game_assets = make_assets(tmp_path / "game", ("a.svg",), record=None)
    pack_assets = make_assets(tmp_path / "pack", ("b.svg", "sub/c.svg"))  # (a file in a folder is recorded by its path)
    game = Game(name="G", assets_dir=game_assets, addons=(AddOn(key="pack", name="Pack", assets_dir=pack_assets),))
    assert integrate(game, []).asset_dirs == (game_assets,)
    assert integrate(game, ["pack"]).asset_dirs == (game_assets, pack_assets)


def test_the_same_file_name_in_two_layers_is_refused_naming_both(tmp_path):
    game_assets = make_assets(tmp_path / "game", ("one.svg",), record=None)
    pack = AddOn(key="pack", name="Pack", assets_dir=make_assets(tmp_path / "pack", ("one.svg",)))
    other = AddOn(key="other", name="Other", assets_dir=make_assets(tmp_path / "other", ("one.svg",)))
    with pytest.raises(AddOnError, match=r"the file 'one.svg' is in the game and in the add-on pack"):
        integrate(Game(name="G", assets_dir=game_assets, addons=(pack,)), ["pack"])
    with pytest.raises(AddOnError, match=r"the file 'one.svg' is in the add-on pack and in the add-on other"):
        integrate(Game(name="G", addons=(pack, other)), ["pack", "other"])
    integrate(Game(name="G", addons=(pack, other)), ["pack"])  # (listing only one is no clash)


def test_an_addons_assets_need_a_record_of_their_license(tmp_path):
    def addon(folder):
        return Game(name="G", addons=(AddOn(key="pack", name="Pack", assets_dir=folder),))

    with pytest.raises(AddOnError, match="needs an ASSETS.md"):
        integrate(addon(make_assets(tmp_path / "none", record=None)), ["pack"])
    integrate(addon(make_assets(tmp_path / "empty", names=(), record=None)), ["pack"])  # (no files, nothing to record)
    integrate(addon(make_assets(tmp_path / "ok", record="CC-BY 4.0")), ["pack"])


def test_every_file_must_be_recorded_with_a_source_and_a_license(tmp_path):
    folder = make_assets(tmp_path / "pack", ("one.svg", "two.svg"))
    (folder / "ASSETS.md").write_text(RECORD.format(files="`one.svg`", source="Drawn", license="MIT"))
    game = Game(name="G", addons=(AddOn(key="pack", name="Pack", assets_dir=folder),))
    with pytest.raises(AddOnError, match="two.svg is not recorded"):
        integrate(game, ["pack"])
    (folder / "ASSETS.md").write_text(RECORD.format(files="`one.svg`, `two.svg`", source="", license="MIT"))
    with pytest.raises(AddOnError, match="the row for one.svg has no source"):
        integrate(game, ["pack"])
    (folder / "ASSETS.md").write_text(RECORD.format(files="`one.svg`, `two.svg`", source="Drawn", license=""))
    with pytest.raises(AddOnError, match="the row for one.svg: no license"):
        integrate(game, ["pack"])
    (folder / "ASSETS.md").write_text("| `one.svg`, `two.svg` | Drawn |\n")
    with pytest.raises(AddOnError, match="needs a source and a license"):
        integrate(game, ["pack"])
    (folder / "ASSETS.md").write_text("Rows are recorded below.\n\n" + RECORD.format(files="`one.svg`, `two.svg`", source="Drawn", license="MIT"))
    integrate(game, ["pack"])


@pytest.mark.parametrize("text", ["MIT", "CC0", "CC-BY 4.0", "CC BY 3.0", "CC-BY-SA 4.0", "OFL", "GPL-3.0", "AGPL-3.0 with the module permission",
                                  "Public domain", "Apache-2.0", "BSD-3-Clause", "The author's own work, released under the AGPL-3.0"])
def test_licenses_compatible_with_the_agpl_are_accepted(text):
    assert license_problem(text) is None


@pytest.mark.parametrize("text", ["", "  ", "unknown", "TBD", "All rights reserved", "CC-BY-NC 4.0", "CC BY-ND 4.0", "CC-BY-NC-SA 4.0", "CC-BY-SA 3.0",
                                  "CC-BY-SA 2.5", "CC-BY-SA", "non-commercial use only", "Free for personal use", "no derivatives", "proprietary"])
def test_licenses_the_repo_cannot_carry_are_refused(text):
    assert license_problem(text)


def test_an_addons_animations_and_color_maps_are_checked(tmp_path):
    folder = make_assets(tmp_path / "pack", ("bad.anim.json",))
    (folder / "bad.anim.json").write_text("{")
    with pytest.raises(AddOnError, match="add-on pack: assets: bad.anim.json: not JSON"):
        integrate(Game(name="G", addons=(AddOn(key="pack", name="Pack", assets_dir=folder),)), ["pack"])


# --- the app -----------------------------------------------------------------------------------------------------

@pytest.fixture
def addons():
    return ["pack"]


@pytest.fixture
def game(tmp_path):
    base_assets = make_assets(tmp_path / "game_assets", ("base.svg",), record=None)
    pack = AddOn(key="pack", name="Pack", seed_dir=make_seed(tmp_path / "pack_seed", items=[TONIC]),
                 assets_dir=make_assets(tmp_path / "pack_assets", ("extra.svg",), record="CC0"))
    unused = AddOn(key="unused", name="Unused", seed_dir=make_seed(tmp_path / "unused_seed", items=[{**HERB, "key": "never"}]),
                   assets_dir=make_assets(tmp_path / "unused_assets", ("unused.svg",), record="CC0"))
    return Game(name="G", seed_dir=make_seed(tmp_path / "seed", items=[HERB]), assets_dir=base_assets, addons=(pack, unused))


def test_a_listed_addons_content_and_assets_join_the_games(app_client):
    from terraforma.testing import in_app_db

    async def keys(session):
        return set((await session.execute(select(Item.key).where(Item.active))).scalars())

    assert in_app_db(app_client, keys) == {"herb", "tonic"}, "the unlisted add-on brings nothing"
    assert [addon.key for addon in app_client.app.state.addons] == ["pack"]
    assert expect(app_client.get("/assets/base.svg"), 200).headers["content-type"].startswith("image/svg")
    expect(app_client.get("/assets/extra.svg"), 200)
    expect(app_client.get("/assets/unused.svg"), 404)
    expect(app_client.get("/assets/%2e%2e/seed/items.json"), 404)
    assert expect(app_client.get("/api/client"), 200).json()["assets"] == "/assets/"


def make_settings(tmp_path, *listed):
    from terraforma.keys import new_key
    from terraforma.settings import Settings

    if not (tmp_path / "keys").exists():
        new_key(tmp_path / "keys")
    return Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/addons.db", session_secret="x" * 32, key_dir=tmp_path / "keys",
                    fight_timer_seconds=0, housekeeping_seconds=0, addons=list(listed))


def test_a_game_with_no_assets_of_its_own_serves_a_listed_addons(tmp_path):
    pack = AddOn(key="pack", name="Pack", assets_dir=make_assets(tmp_path / "assets", ("extra.svg",)))
    with TestClient(create_app(make_settings(tmp_path), Game(name="G", addons=(pack,)))) as client:
        assert expect(client.get("/api/client"), 200).json()["assets"] is None
        expect(client.get("/assets/extra.svg"), 404)
    with TestClient(create_app(make_settings(tmp_path, "pack"), Game(name="G", addons=(pack,)))) as client:
        assert expect(client.get("/api/client"), 200).json()["assets"] == "/assets/"
        expect(client.get("/assets/extra.svg"), 200)


def test_the_server_will_not_start_with_a_clash_or_an_unsupported_addon(tmp_path):
    clash = Game(name="G", seed_dir=make_seed(tmp_path / "s", items=[HERB]),
                 addons=(AddOn(key="pack", name="P", seed_dir=make_seed(tmp_path / "p", items=[HERB])),))
    with pytest.raises(AddOnError, match="not one this game supports"):
        create_app(make_settings(tmp_path, "pack"), Game(name="G"))
    create_app(make_settings(tmp_path), clash)  # (not listed: no clash)
    # A clash in the seed shows when the app starts (the seed is read then), like any other seed problem.
    with pytest.raises(AddOnError, match="the key 'herb' is in the game and in the add-on pack"):
        with TestClient(create_app(make_settings(tmp_path, "pack"), clash)):
            pass


def test_a_game_without_addons_is_as_before():
    integrated = integrate(EXAMPLE, [])
    assert integrated.addons == () and integrated.asset_dirs == (EXAMPLE.assets_dir,) and integrated.seed_dirs == ((None, EXAMPLE.seed_dir),)
    assert integrate(None, []).asset_dirs == ()
