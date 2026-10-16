"""Add-ons (slice 2): the sections an add-on groups its rows in, the component states in the settings document, what the check
refuses, and how a commit switches the rows (job picks, shops, drops, spawns and abilities all read ``active``)."""

import dataclasses
import json

import pytest
from sqlalchemy import select

from terraforma.addons import AddOn, AddOnError, SectionNode, integrate, layer_seeds
from terraforma.challenge import service as challenge
from terraforma.content.models import Ability, DropTable, Item, Job, Monster
from terraforma.example import GAME as EXAMPLE
from terraforma.fights.build import known_drop_tables
from terraforma.gamestate import service as state_service
from terraforma.gamestate.components import Components
from terraforma.gamestate.document import hash_of
from terraforma.heroes.service import list_jobs
from terraforma.market.hooks import Market
from terraforma.models import Account
from terraforma.testing import in_app_db

from .helpers import expect
from .test_addons import make_seed
from .test_towns import sign_in

pytestmark = pytest.mark.platform

ABILITY = {"key": "pack_fire", "name": "Fire", "kind": "skill", "mp_cost": 2, "description": "A burst of flame.", "section": "magic",
           "effect": {"effect": "hurt", "targets": "individual", "base": 8, "added": 6}}
TONIC = {"key": "pack_tonic", "name": "Pack tonic", "price": 5, "description": "A sweet draught.", "one_use": True, "section": "tonics",
         "use_effect": {"effect": "heal", "targets": "individual", "base": 10, "added": 0}}
ELIXIR = {**TONIC, "key": "pack_elixir", "name": "Pack elixir", "section": "potions"}
PLAIN = {**TONIC, "key": "pack_plain", "name": "Pack plain", "section": None}
DROPS = {"key": "pack_drops", "name": "Pack drops", "entries": [{"item": "pack_tonic", "chance": 3000}], "section": "beasts"}
SLIME = {"key": "pack_slime", "name": "Pack slime", "personality": "slime", "xp_reward": 5, "gold_reward": 3, "drops": ["pack_drops"],
         "abilities": ["pack_fire"], "section": "beasts",
         "stats": {"HP": 14, "MP": 0, "Speed": 4, "Accuracy": 6, "Strength": 3, "Dodge": 1, "Block": 0}, "ai": {"action": 0, "goal": 0, "target": 0, "experience": 50}}
MAGE = {"key": "pack_mage", "name": "Pack mage", "xp_needed": 20, "stat_growth": {"HP": 10}, "section": "classes",
        "abilities": [{"ability": "pack_fire", "level": 1}]}

SECTIONS = {
    "abilities": (SectionNode("magic", "Magic"),),
    "items": (SectionNode("potions", "Potions", (SectionNode("tonics", "Tonics"),)),),
    "drop_tables": (SectionNode("beasts", "Beasts"),),
    "monsters": (SectionNode("beasts", "Beasts"),),
    "jobs": (SectionNode("classes", "Classes"),),
}


def pack(tmp_path, **changes):
    rows = {"abilities": [ABILITY], "items": [TONIC, ELIXIR, PLAIN], "drop_tables": [DROPS], "monsters": [SLIME], "jobs": [MAGE]}
    return AddOn(key="pack", name="Pack", seed_dir=make_seed(tmp_path / "pack_seed", **(rows | changes.pop("rows", {}))),
                 sections=changes.pop("sections", SECTIONS), **changes)


def components(tmp_path):
    """The Components section of the example game with the pack add-on listed, and the game."""
    game = dataclasses.replace(EXAMPLE, addons=(pack(tmp_path),))
    return Components(integrate(game, ["pack"]), game), game


def on(*rows, kind):
    return {kind: {key: "on" for key in rows}}


# --- the sections an add-on declares -------------------------------------------------------------------------------

def test_a_row_may_sit_in_any_section_its_addon_declares_at_any_depth(tmp_path):
    seed = integrate(dataclasses.replace(EXAMPLE, addons=(pack(tmp_path),)), ["pack"]).load_seed()
    assert {row["key"]: row.get("section") for row in seed.rows["items"] if row["key"].startswith("pack_")} == {
        "pack_tonic": "tonics", "pack_elixir": "potions", "pack_plain": None}


@pytest.mark.parametrize("section, why", [("nowhere", "the section 'nowhere', which the add-on does not declare for items"), ("beasts", "the section 'beasts'")])
def test_a_row_in_a_section_the_addon_does_not_declare_for_its_kind_stops_the_server(tmp_path, section, why):
    broken = pack(tmp_path, rows={"items": [{**TONIC, "section": section}]})
    with pytest.raises(AddOnError, match=why):
        integrate(dataclasses.replace(EXAMPLE, addons=(broken,)), ["pack"]).load_seed()


def test_only_an_addons_rows_are_in_sections(tmp_path):
    seed = make_seed(tmp_path / "game", items=[{**TONIC, "key": "game_tonic"}])
    with pytest.raises(AddOnError, match="'game_tonic' is the game's and has a section"):
        layer_seeds(((None, seed),))


def test_sections_are_for_kinds_that_can_be_switched_and_keys_are_used_once_per_kind(tmp_path):
    game = lambda sections: dataclasses.replace(EXAMPLE, addons=(pack(tmp_path, sections=sections),))  # noqa: E731
    with pytest.raises(AddOnError, match="sections for 'maps', which cannot be switched"):
        integrate(game({"maps": (SectionNode("a", "A"),)}), ["pack"])
    with pytest.raises(AddOnError, match="the items section 'a' is used twice"):
        integrate(game({"items": (SectionNode("a", "A", (SectionNode("a", "Again"),)),)}), ["pack"])
    with pytest.raises(AddOnError, match="'Bad Key' is not a key"):
        integrate(game({"items": (SectionNode("Bad Key", "Bad"),)}), ["pack"])
    with pytest.raises(AddOnError, match="needs a name"):
        integrate(game({"items": (SectionNode("a", " "),)}), ["pack"])
    integrate(game({"items": (SectionNode("a", "A"),), "monsters": (SectionNode("a", "A"),)}), ["pack"])  # (one key may serve two kinds)


# --- the states in the document ---------------------------------------------------------------------------------------

def test_the_section_starts_empty_and_every_component_starts_off(tmp_path):
    section, _ = components(tmp_path)
    assert section.default() == {"states": {}} and section.check(section.default(), {}) == []
    assert section.kinds("pack") == ["abilities", "items", "drop_tables", "jobs", "monsters"]


def test_a_bulk_switch_covers_an_addon_a_kind_or_a_section_and_the_sections_inside_it(tmp_path):
    section, _ = components(tmp_path)
    empty = section.default()
    assert section.rows_of("pack", "items") == ["pack_tonic", "pack_elixir", "pack_plain"]
    assert section.rows_of("pack", "items", "potions") == ["pack_tonic", "pack_elixir"], "(tonics is inside potions)"
    assert section.rows_of("pack", "items", "tonics") == ["pack_tonic"]
    assert section.switch(empty, "pack", True, "items", "tonics") == {"states": {"pack": {"items": {"pack_tonic": "on"}}}}
    everything = section.switch(empty, "pack", True)
    assert set(everything["states"]["pack"]) == {"abilities", "items", "drop_tables", "jobs", "monsters"} and section.check(everything, {}) == []
    kind = section.switch(empty, "pack", True, "items")
    assert kind == {"states": {"pack": {"items": {key: "on" for key in ("pack_tonic", "pack_elixir", "pack_plain")}}}}
    off = section.switch(kind, "pack", False, "items", "potions")
    assert off == {"states": {"pack": {"items": {"pack_plain": "on"}}}}
    assert section.switch(off, "pack", False) == empty, "(nothing is left behind: the same states are the same document)"
    assert kind == section.switch(empty, "pack", True, "items"), "(a switch makes a new value)"


def test_the_same_states_make_the_same_hash_whatever_order_they_were_switched_in(tmp_path):
    section, _ = components(tmp_path)
    one = section.switch(section.switch(section.default(), "pack", True, "items", "tonics"), "pack", True, "abilities")
    two = section.switch(section.switch(section.default(), "pack", True, "abilities"), "pack", True, "items", "tonics")
    assert hash_of(one) == hash_of(two) != hash_of(section.default())


def test_a_bulk_switch_of_what_the_addon_does_not_have_is_refused(tmp_path):
    section, _ = components(tmp_path)
    for args, why in ((("pack", True, "maps"), "brought no maps"), (("pack", True, "items", "magic"), "declares no items section 'magic'"),
                      (("pack", True, None, "magic"), "belongs to a kind"), (("other", True), "brought no components")):
        with pytest.raises(ValueError, match=why):
            section.switch(section.default(), *args)


@pytest.mark.parametrize("value, why", [
    ([], "exactly 'states'"),
    ({"states": [], }, "exactly 'states'"),
    ({"states": {}, "extra": 1}, "exactly 'states'"),
    ({"states": {"ghost": {"items": {"x": "on"}}}}, "the add-on 'ghost' is not listed"),
    ({"states": {"pack": {}}}, "pack: must be an object with the kinds"),
    ({"states": {"pack": {"maps": {"x": "on"}}}}, "pack: the add-on brought no maps"),
    ({"states": {"pack": {"items": {}}}}, "items must be an object with the rows that are on"),
    ({"states": {"pack": {"items": {"nothing": "on"}}}}, "it brought no items row 'nothing'"),
    ({"states": {"pack": {"items": {"herb": "on"}}}}, "it brought no items row 'herb'"),
    ({"states": {"pack": {"items": {"pack_tonic": "off"}}}}, "items 'pack_tonic' is 'off'; the only state is 'on'"),
])
def test_the_check_names_what_is_wrong_with_the_states(tmp_path, value, why):
    section, _ = components(tmp_path)
    assert any(why in problem for problem in section.check(value, {})), section.check(value, {})


def test_a_component_that_needs_another_may_not_be_on_while_that_one_is_off(tmp_path):
    section, _ = components(tmp_path)
    just = lambda kind, *keys: {"states": {"pack": on(*keys, kind=kind)}}  # noqa: E731
    assert section.check(just("monsters", "pack_slime"), {}) == [
        "pack: monsters 'pack_slime' is on, but it needs abilities 'pack_fire', which is off",
        "pack: monsters 'pack_slime' is on, but it needs drop_tables 'pack_drops', which is off"]
    assert section.check(just("jobs", "pack_mage"), {}) == ["pack: jobs 'pack_mage' is on, but it needs abilities 'pack_fire', which is off"]
    assert section.check(just("drop_tables", "pack_drops"), {}) == ["pack: drop_tables 'pack_drops' is on, but it needs items 'pack_tonic', which is off"]
    whole = section.switch(section.default(), "pack", True)
    assert section.check(whole, {}) == []
    assert section.check(section.switch(whole, "pack", False, "abilities"), {}) == [
        "pack: jobs 'pack_mage' is on, but it needs abilities 'pack_fire', which is off",
        "pack: monsters 'pack_slime' is on, but it needs abilities 'pack_fire', which is off"]
    assert section.check(section.switch(whole, "pack", False, "monsters"), {}) == [], "(turning off the user of a component is fine)"


def test_a_game_with_no_addons_has_an_empty_section_that_reads_no_seed():
    section = Components(integrate(EXAMPLE, []), EXAMPLE)
    assert section.check(section.default(), {}) == [] and section.facts().owned == {}
    assert section.check({"states": {"pack": on("x", kind="items")}}, {}) == ["the add-on 'pack' is not listed in the settings"]


# --- the app ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def addons():
    return ["pack"]


@pytest.fixture
def game(tmp_path):
    return dataclasses.replace(EXAMPLE, addons=(pack(tmp_path),))


async def make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


def an_admin(client):
    root = sign_in(client, "Root")
    in_app_db(client, lambda db: make_admin(db, "Root"))
    return root


def commit(client, root, value, note=""):
    """Edits the components section of the draft to $value and commits it."""
    expect(client.put("/api/admin/state/planned/sections/components", json={"value": value}, headers=root), 200)
    checked = expect(client.post("/api/admin/state/check", headers=root), 200).json()
    assert checked["problems"] == []
    return expect(client.post("/api/admin/state/commit", json={"hash": checked["hash"], "note": note}, headers=root), 200).json()


async def what_is_on(db):
    return {
        "jobs": {job.key for job in await list_jobs_rows(db)},
        "items": {item.key for item in await db.scalars(select(Item).where(Item.active.is_(True), Item.key.like("pack_%")))},
        "stock": {item.key for item in await Market().stock(db, "shop", 1000) if item.key.startswith("pack_")},
        "drops": set((await known_drop_tables(db, {"pack_drops"})).keys()),
        "monsters": {row.key for row in await db.scalars(select(Monster).where(Monster.active.is_(True), Monster.key.like("pack_%")))},
        "abilities": {row.key for row in await db.scalars(select(Ability).where(Ability.active.is_(True), Ability.key.like("pack_%")))},
    }


async def list_jobs_rows(db):
    return [job for job in await db.scalars(select(Job).where(Job.active.is_(True))) if job.key.startswith("pack_")]


NOTHING = {"jobs": set(), "items": set(), "stock": set(), "drops": set(), "monsters": set(), "abilities": set()}


def test_a_listed_addons_components_start_off_everywhere_they_could_be_used(app_client):
    assert in_app_db(app_client, what_is_on) == NOTHING
    assert [job["key"] for job in expect(app_client.get("/api/jobs", headers=sign_in(app_client, "Mike")), 200).json()].count("pack_mage") == 0


def test_a_commit_switches_what_is_offered_sold_dropped_spawned_and_picked(app_client):
    root = an_admin(app_client)
    section = app_client.app.state.state_sections["components"]
    everything = section.switch(section.default(), "pack", True)
    expect(app_client.put("/api/admin/state/planned/sections/components", json={"value": everything}, headers=root), 200)
    assert in_app_db(app_client, what_is_on) == NOTHING, "(the planned state is only a plan)"
    commit(app_client, root, everything, "pack on")
    assert in_app_db(app_client, what_is_on) == {"jobs": {"pack_mage"}, "items": {"pack_tonic", "pack_elixir", "pack_plain"}, "stock": {"pack_tonic", "pack_elixir", "pack_plain"},
                                                 "drops": {"pack_drops"}, "monsters": {"pack_slime"}, "abilities": {"pack_fire"}}
    assert "pack_mage" in [job["key"] for job in expect(app_client.get("/api/jobs", headers=root), 200).json()]
    expect(app_client.post("/api/teams", json={"name": "Mages", "heroes": [{"name": "Merlin", "job": "pack_mage"}]}, headers=root), 201)
    # (the drop table needs the tonic and the monster needs the drop table, so they go off first)
    no_potions = section.switch(section.switch(section.switch(everything, "pack", False, "monsters"), "pack", False, "drop_tables"), "pack", False, "items", "potions")
    commit(app_client, root, no_potions, "no potions")
    now = in_app_db(app_client, what_is_on)
    assert (now["items"], now["stock"], now["jobs"], now["monsters"], now["drops"]) == ({"pack_plain"}, {"pack_plain"}, {"pack_mage"}, set(), set())
    commit(app_client, root, section.default(), "pack off")
    assert in_app_db(app_client, what_is_on) == NOTHING
    expect(app_client.post("/api/teams", json={"name": "More mages", "heroes": [{"name": "Morgana", "job": "pack_mage"}]}, headers=root), 404)
    commit(app_client, root, everything, "pack back on")
    assert in_app_db(app_client, what_is_on)["jobs"] == {"pack_mage"}, "(turning it back restores it)"


def test_no_new_use_leaves_what_exists_working(app_client):
    root = an_admin(app_client)
    section = app_client.app.state.state_sections["components"]
    everything = section.switch(section.default(), "pack", True)
    commit(app_client, root, everything)
    team = expect(app_client.post("/api/teams", json={"name": "Mages", "heroes": [{"name": "Merlin", "job": "pack_mage"}]}, headers=root), 201).json()
    commit(app_client, root, section.default())
    heroes = expect(app_client.get("/api/heroes", headers=root), 200).json()
    assert [(hero["name"], hero["job"]) for hero in heroes] == [("Merlin", "pack_mage")], "(the hero keeps its job and its team)"
    assert team["members"][0]["name"] == "Merlin"


def test_a_commit_with_an_unmet_need_is_refused_and_changes_nothing(app_client):
    root = an_admin(app_client)
    section = app_client.app.state.state_sections["components"]
    expect(app_client.put("/api/admin/state/planned/sections/components", json={"value": {"states": {"pack": on("pack_mage", kind="jobs")}}}, headers=root), 200)
    checked = expect(app_client.post("/api/admin/state/check", headers=root), 200).json()
    assert checked["problems"] == [{"section": "components", "message": "pack: jobs 'pack_mage' is on, but it needs abilities 'pack_fire', which is off"}]
    expect(app_client.post("/api/admin/state/commit", json={"hash": checked["hash"]}, headers=root), 422)
    assert in_app_db(app_client, what_is_on) == NOTHING and section.default() == {"states": {}}


def test_every_start_sets_the_rows_to_the_live_states_whatever_happened_meanwhile(app_client):
    root = an_admin(app_client)
    section = app_client.app.state.state_sections["components"]
    commit(app_client, root, section.switch(section.default(), "pack", True, "items", "tonics"))

    async def drift(db):
        (await db.scalar(select(Item).where(Item.key == "pack_tonic"))).active = False
        (await db.scalar(select(Item).where(Item.key == "pack_plain"))).active = True

    in_app_db(app_client, drift)
    assert in_app_db(app_client, what_is_on)["items"] == {"pack_plain"}
    in_app_db(app_client, lambda db: state_service.apply_live(db, app_client.app.state.state_sections))
    assert in_app_db(app_client, what_is_on)["items"] == {"pack_tonic"}


def test_the_state_is_in_the_snapshot_and_a_revert_goes_back_to_it(app_client):
    root = an_admin(app_client)
    section = app_client.app.state.state_sections["components"]
    first = expect(app_client.get("/api/admin/state", headers=root), 200).json()["live"]["hash"]
    tonics = section.switch(section.default(), "pack", True, "items", "tonics")
    done = commit(app_client, root, tonics)
    assert done["live"]["hash"] != first
    live = expect(app_client.get("/api/admin/state/live", headers=root), 200).json()["document"]["sections"]["components"]
    assert live == tonics
    expect(app_client.put("/api/admin/state/planned/sections/components", json={"value": section.default()}, headers=root), 200)
    reverted = expect(app_client.post("/api/admin/state/revert", headers=root), 200).json()
    assert reverted["planned"]["differs"] is False and reverted["live"]["hash"] == done["live"]["hash"]
    assert in_app_db(app_client, what_is_on)["items"] == {"pack_tonic"}


def test_the_seed_file_carries_a_section_but_the_table_does_not(app_client):
    assert not hasattr(Item, "section")
    layered = integrate(app_client.app.state.game, ["pack"]).load_seed()
    assert json.loads(json.dumps(layered.rows["monsters"][-1]))["section"] == "beasts"
