"""Map events: objects (chests, doors, signs) and edges run scripts like an NPC's dialog, the warp tag moves a party, and the
page can see them on the map and in the nearby list, on every database."""

import copy
import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.game import Game
from terraforma.heroes import inventory, service as heroes
from terraforma.heroes.models import Hero
from terraforma.maps import objects, travel, walking
from terraforma.maps.models import MapObject
from terraforma.fights.rules import Rules
from terraforma.models import Map
from terraforma.npcs import service as npcs
from terraforma.npcs.hooks import Npcs
from terraforma.npcs.models import NpcTalk
from terraforma.parties import service as parties
from terraforma.reach import service as nearby
from terraforma.reach.hooks import Reach
from terraforma.parties.models import Party
from terraforma.testing import in_app_db
from terraforma.towns import service as towns
from terraforma.towns.hooks import Towns

from .helpers import expect
from .test_npcs import sign_in

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
NPCS, REACH, RULES = Npcs(), Reach(), Rules()

CHEST = "`add_item,potion,1,full`A potion!`end``label,full`You can carry no more."
DOOR = "`question,Go down,down,Stay,end`The stairs lead down.\n`label,down``warp,cellar,1,1,end`"
SIGN = "Welcome to the yard."
NORTH = "`question,Cross,cross,Turn back,end`Beyond lie the wilds.\n`label,cross``warp,cellar,0,0,end`"
WEST = "A wall of thorns."

# A yard of open ground, 5 wide and 3 high: a chest, a door and a sign, and events on the north and west edges.
YARD = {
    "key": "yard", "name": "The Yard", "tileset": [{"name": "grass"}], "tiles": [[0] * 5, [0] * 5, [0] * 5],
    "objects": [
        {"key": "chest", "name": "Old chest", "kind": "chest", "x": 3, "y": 1, "script": CHEST},
        {"key": "door", "name": "Cellar door", "kind": "door", "x": 4, "y": 2, "script": DOOR},
        {"key": "sign", "name": "Signpost", "kind": "sign", "action": "read", "x": 0, "y": 0, "script": SIGN},
    ],
    "edges": {"north": NORTH, "west": WEST},
}
CELLAR = {"key": "cellar", "name": "The Cellar", "tileset": [{}, {"passable": False}], "tiles": [[0, 0, 0], [0, 0, 0], [0, 0, 1]]}
SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}],
    "items": [{"key": "potion", "name": "Potion"}],
    "maps": [YARD, CELLAR],
}


def seed(**changes):
    data = copy.deepcopy(SEED)
    data["maps"][0].update(changes)
    return data


@pytest.fixture
async def seeded(db):
    await load_content(db, SEED)
    await db.commit()
    return db


async def crew(db, name="Mike", where="yard", at=(2, 1)):
    """A player with a hero on a team in a party that stands at $at on the map $where; its heroes stand there too."""
    owner = await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
    team = await heroes.create_team(db, owner, f"{name}'s team")
    hero = await heroes.create_hero(db, owner, f"{name}-hero", "fighter")
    await heroes.add_to_team(db, owner, team.id, hero.id)
    party = await parties.create_party(db, team.id, 20)
    party.map_id, party.x, party.y = (await db.scalar(select(Map).where(Map.name == where))).id, *at
    await db.flush()
    await travel.move_heroes(db, party)
    await db.refresh(hero)
    return owner, hero, party


async def revision(db):
    """The yard's revision, read afresh (a load rewrites rows with bulk SQL)."""
    return await db.scalar(select(Map.revision).where(Map.name == "yard").execution_options(populate_existing=True))


async def on_map(db, key):
    return await db.scalar(select(MapObject).where(MapObject.key == key))


# --- checking the seed -----------------------------------------------------------------------------------------------

def test_a_good_map_with_events_checks_and_fills_in_defaults():
    yard = check_seed(SEED)["maps"][0]
    chest, door, sign = yard.objects
    assert (chest.kind, chest.action) == ("chest", "open") and sign.action == "read" and door.script == DOOR
    assert yard.edges == {"north": NORTH, "west": WEST}


@pytest.mark.parametrize("changes, message", [
    ({"wrap_y": True}, "north edge wraps round"),
    ({"wrap_x": True}, "west edge wraps round"),
    ({"edges": {"up": "Hi."}}, "edges"),
    ({"edges": {"north": "`jump,nowhere`"}}, "edges.north: at position 0: there is no label 'nowhere'"),
    ({"edges": {"north": "`warp,cellar,1,1`"}}, "edges.north: at position 0: warp tag takes 4 parameters"),
    ({"objects": [{"key": "a", "name": "A", "x": 5, "y": 0, "script": "Hi."}]}, "stands off the map"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 3, "script": "Hi."}]}, "stands off the map"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 0, "script": "Hi."}] * 2}, "used twice"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 0, "script": ""}]}, "script"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 0, "script": "`team,x,end`"}]}, "script"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 0, "script": "Hi.", "action": "Open!"}]}, "action"),
    ({"objects": [{"key": "a", "name": "A", "x": -1, "y": 0, "script": "Hi."}]}, "x"),
    ({"objects": [{"key": "a", "name": "A", "x": 0, "y": 0, "script": "Hi.", "colour": "red"}]}, "colour"),
])
def test_a_bad_object_or_edge_is_refused_naming_the_problem(changes, message):
    with pytest.raises(ContentError, match=message):
        check_seed(seed(**changes))


@pytest.mark.parametrize("warp, message", [
    ("`warp,dungeon,0,0,end`", "warps to 'dungeon', which maps.json doesn't have"),
    ("`warp,cellar,3,0,end`", "warps to \\(3, 0\\), which is off 'cellar'"),
    ("`warp,cellar,0,3,end`", "warps to \\(0, 3\\), which is off 'cellar'"),
    ("`warp,cellar,2,2,end`", "can't be stood on"),
])
def test_a_warp_must_land_on_a_tile_of_a_map_that_exists(warp, message):
    with pytest.raises(ContentError, match=message):
        check_seed(seed(edges={"north": warp}))


def test_a_warp_to_the_default_hub_is_allowed_without_a_hub_row():
    check_seed(seed(edges={"north": "`warp,hub,10,10,end`"}))


# --- loading ---------------------------------------------------------------------------------------------------------

async def test_loading_puts_objects_and_edges_on_the_map(seeded):
    yard = await seeded.scalar(select(Map).where(Map.name == "yard"))
    rows = (await seeded.scalars(select(MapObject).where(MapObject.map_id == yard.id).order_by(MapObject.key))).all()
    assert [row.key for row in rows] == ["chest", "door", "edge-north", "edge-west", "sign"]
    chest, door, north, west, sign = rows
    assert (chest.name, chest.kind, chest.action, chest.x, chest.y, chest.dialog, chest.edge) == ("Old chest", "chest", "open", 3, 1, CHEST, None)
    assert (sign.action, door.kind) == ("read", "door")
    assert (north.kind, north.edge, north.dialog) == ("edge", "north", NORTH) and west.edge == "west"
    assert yard.revision == 1


async def test_loading_again_changes_nothing_and_a_new_script_does_not_move_the_revision(seeded):
    before = (await seeded.scalars(select(MapObject.id).order_by(MapObject.id))).all()
    await load_content(seeded, SEED)
    assert (await seeded.scalars(select(MapObject.id).order_by(MapObject.id))).all() == before and await revision(seeded) == 1
    changed = copy.deepcopy(SEED)
    changed["maps"][0]["objects"][0]["script"] = "It is empty."
    await load_content(seeded, changed)
    assert (await on_map(seeded, "chest")).dialog == "It is empty." and await revision(seeded) == 1, "a page can't see a script"


@pytest.mark.parametrize("change", ["move", "rename", "edge"])
async def test_what_a_page_can_see_going_stale_raises_the_revision(seeded, change):
    changed = copy.deepcopy(SEED)
    if change == "move":
        changed["maps"][0]["objects"][0]["x"] = 2
    elif change == "rename":
        changed["maps"][0]["objects"][0]["name"] = "Chest"
    else:
        del changed["maps"][0]["edges"]["west"]
    await load_content(seeded, changed)
    assert await revision(seeded) == 2


async def test_an_object_the_seed_drops_goes_with_the_conversations_heroes_had_with_it(seeded):
    _, hero, _ = await crew(seeded)
    chest = await on_map(seeded, "chest")
    door = await on_map(seeded, "door")
    hero.x, hero.y = 4, 1
    await objects.use(seeded, NPCS, REACH, hero, door.id)  # asks a question, so the conversation stays open
    assert await seeded.scalar(select(func.count()).select_from(NpcTalk)) == 1
    changed = copy.deepcopy(SEED)
    del changed["maps"][0]["objects"][1]
    await load_content(seeded, changed)
    assert await on_map(seeded, "door") is None and await on_map(seeded, "chest") is not None and chest.id is not None
    assert await seeded.scalar(select(func.count()).select_from(NpcTalk)) == 0


# --- using an object -------------------------------------------------------------------------------------------------

async def test_a_hero_opens_a_chest_in_reach_and_gets_what_is_in_it(seeded):
    _, hero, _ = await crew(seeded)
    frame = await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "chest")).id)
    assert frame["object"]["kind"] == "chest" and frame["events"] == [{"type": "text", "text": "A potion!"}]
    assert frame["ended"] and frame["prompt"] is None and frame["window"] is False, "plain text opens no dialog window"
    assert [(item.key, stack.qty) for stack, item in await inventory.stacks(seeded, hero)] == [("potion", 1)]
    assert await seeded.scalar(select(func.count()).select_from(NpcTalk)) == 0


async def test_a_script_that_asks_something_opens_the_dialog_window_and_goes_on_with_the_answer(seeded):
    _, hero, party = await crew(seeded, at=(4, 1))
    frame = await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "door")).id)
    assert frame["window"] is True and frame["ended"] is False
    assert frame["prompt"]["type"] == "choice" and [option["text"] for option in frame["prompt"]["options"]] == ["Go down", "Stay"]
    assert (await npcs.current(seeded, hero))["object"]["key"] == "door", "the page can pick the conversation up again"
    stay = await npcs.answer(seeded, NPCS, REACH, hero, 1)
    assert stay["ended"] and (party.x, party.y) == (4, 1)


async def test_a_door_moves_the_party_and_its_heroes_to_the_other_map(seeded):
    _, hero, party = await crew(seeded, at=(4, 1))
    party.steps, party.route = 7, {"tiles": [[4, 1], [3, 1]], "at": 0, "revision": 1}
    await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "door")).id)
    done = await npcs.answer(seeded, NPCS, REACH, hero, 0)
    assert done["ended"] and done["events"] == [] and await seeded.scalar(select(func.count()).select_from(NpcTalk)) == 0
    cellar = await seeded.scalar(select(Map).where(Map.name == "cellar"))
    await seeded.refresh(hero)
    assert (party.map_id, party.x, party.y) == (cellar.id, 1, 1) and (hero.map_id, hero.x, hero.y) == (cellar.id, 1, 1)
    assert party.steps == 0 and party.route is None, "a party new to a map starts its safe steps over"


async def test_a_warp_can_not_be_done_by_a_party_that_cannot_go_and_says_why_in_the_script(seeded, monkeypatch):
    async def busy(session, party):
        return True

    monkeypatch.setattr(travel, "in_fight", busy)
    text = "`warp,cellar,1,1,stuck`Gone.`end``label,stuck`Not now."
    await seeded.execute(MapObject.__table__.update().where(MapObject.key == "chest").values(dialog=text))
    _, hero, party = await crew(seeded)
    chest = await seeded.get(MapObject, (await on_map(seeded, "chest")).id, populate_existing=True)
    frame = await objects.use(seeded, NPCS, REACH, hero, chest.id)
    assert frame["events"] == [{"type": "text", "text": "Not now."}] and (party.x, party.y) == (2, 1)


async def test_an_object_out_of_reach_or_no_longer_there_is_refused(seeded):
    _, hero, _ = await crew(seeded)  # the door is two tiles off
    with pytest.raises(npcs.NpcError, match="too far"):
        await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "door")).id)
    with pytest.raises(objects.NoSuchObject):
        await objects.use(seeded, NPCS, REACH, hero, 9999)
    with pytest.raises(objects.NoSuchObject):
        await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "edge-north")).id)
    cellar = await seeded.scalar(select(Map).where(Map.name == "cellar"))
    hero.map_id = cellar.id
    with pytest.raises(npcs.NpcError, match="not here"):
        await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "chest")).id)


async def test_a_conversation_ends_when_the_hero_is_no_longer_in_reach(seeded):
    _, hero, _ = await crew(seeded, at=(4, 1))
    await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "door")).id)
    hero.x = 0
    with pytest.raises(npcs.NpcError, match="too far"):
        await npcs.answer(seeded, NPCS, REACH, hero, 0)
    assert await seeded.scalar(select(func.count()).select_from(NpcTalk)) == 0


async def test_a_game_decides_who_may_open_what(seeded):
    class Locked(Reach):
        async def map_object(self, session, action, actor, obj):
            return "it is locked" if obj.key == "chest" else await super().map_object(session, action, actor, obj)

    _, hero, _ = await crew(seeded)
    with pytest.raises(npcs.NpcError, match="locked"):
        await objects.use(seeded, NPCS, Locked(), hero, (await on_map(seeded, "chest")).id)
    assert [each["key"] for each in (await nearby.nearby(seeded, Locked(), RULES, hero, "open"))["nearby"]] == []


# --- the nearby list -------------------------------------------------------------------------------------------------

async def test_the_nearby_list_shows_the_objects_whose_action_it_is_that_are_in_reach(seeded):
    _, hero, _ = await crew(seeded, at=(1, 0))
    listing = await nearby.nearby(seeded, REACH, RULES, hero, "open")
    assert listing["nearby"] == []  # the chest is two tiles off and the sign's action is read
    hero.x, hero.y = 3, 2
    listing = await nearby.nearby(seeded, REACH, RULES, hero, "open")
    assert [(each["kind"], each["key"], each["object"], each["distance"]) for each in listing["nearby"]] == [("object", "door", "door", 1), ("object", "chest", "chest", 1)]
    hero.x, hero.y = 1, 0
    assert [each["key"] for each in (await nearby.nearby(seeded, REACH, RULES, hero, "read"))["nearby"]] == ["sign"]
    assert (await nearby.nearby(seeded, REACH, RULES, hero, "talk"))["nearby"] == [], "a chest is not talked to"


async def test_objects_come_after_npcs_in_the_nearby_list(seeded):
    _, hero, _ = await crew(seeded, at=(3, 2))
    await npcs.place_npc(seeded, "keeper", "Keeper", hero.map_id, 3, 2, "Hi.")
    await seeded.execute(MapObject.__table__.update().where(MapObject.key == "sign").values(action="open", x=3, y=2))
    listing = await nearby.nearby(seeded, REACH, RULES, hero, "open")
    assert [each["kind"] for each in listing["nearby"]] == ["npc", "object", "object", "object"]


# --- walking off an edge ---------------------------------------------------------------------------------------------

def run_edge(db):
    async def run(hero, edge):
        return await npcs.start(db, NPCS, hero, edge)

    return run


async def test_a_route_may_end_off_an_edge_that_has_an_event_and_nowhere_else(seeded):
    owner, _, party = await crew(seeded, at=(1, 1))
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(-1, 1)])
    assert view["route"] == [[1, 1], [0, 1], [-1, 1]]
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(1, -1)])
    assert view["route"] == [[1, 1], [1, 0], [1, -1]]
    for tile in [(5, 1), (1, 3), (-2, 1), (-1, -1)]:  # no event east or south, and two tiles off is not the edge
        with pytest.raises(walking.WalkError, match="off the map"):
            await walking.plan(seeded, RULES, owner.id, party.id, [tile])
    with pytest.raises(walking.WalkError, match="ends where it leaves"):
        await walking.plan(seeded, RULES, owner.id, party.id, [(-1, 1), (0, 1)])
    with pytest.raises(walking.WalkError, match="no way off the map"):
        await walking.plan(seeded, type("Short", (Rules,), {"route_limit": 1})(), owner.id, party.id, [(-1, 1)])


async def test_stepping_off_an_edge_runs_its_script_and_the_party_stays_put(seeded):
    owner, hero, party = await crew(seeded, at=(0, 1))
    await walking.plan(seeded, RULES, owner.id, party.id, [(-1, 1)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, -1, 1, run_edge(seeded))
    assert answer["confirmed"] and answer["done"] and answer["edge"] == "west" and (answer["x"], answer["y"]) == (0, 1)
    assert answer["dialog"]["events"] == [{"type": "text", "text": "A wall of thorns."}] and answer["dialog"]["window"] is False
    assert answer["dialog"]["object"]["kind"] == "edge" and (party.x, party.y, party.route) == (0, 1, None)


async def test_an_edge_event_that_asks_something_goes_on_by_dialog_and_may_warp_the_party(seeded):
    owner, hero, party = await crew(seeded, at=(1, 0))
    await walking.plan(seeded, RULES, owner.id, party.id, [(1, -1)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, 1, -1, run_edge(seeded))
    assert answer["dialog"]["window"] is True and answer["dialog"]["prompt"]["type"] == "choice"
    assert (await npcs.current(seeded, hero))["object"]["key"] == "edge-north"
    await npcs.answer(seeded, NPCS, REACH, hero, 0)
    cellar = await seeded.scalar(select(Map).where(Map.name == "cellar"))
    assert (party.map_id, party.x, party.y) == (cellar.id, 0, 0) and (await npcs.current(seeded, hero)) == {"talking": False}


async def test_the_edge_script_runs_for_the_leaders_hero(seeded):
    owner, _, party = await crew(seeded, at=(1, 0))
    friend = await create_account(seeded, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    team = await heroes.create_team(seeded, friend, "Zed's team")
    hero = await heroes.create_hero(seeded, friend, "Zed-hero", "fighter")
    await heroes.add_to_team(seeded, friend, team.id, hero.id)
    await parties.join_party(seeded, party.id, team.id, 20)
    await walking.plan(seeded, RULES, owner.id, party.id, [(1, -1)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, 1, -1, run_edge(seeded))
    assert answer["edge"] == "north"
    talk = await seeded.scalar(select(NpcTalk))
    assert (await seeded.get(type(hero), talk.hero_id)).account_id == owner.id


async def test_an_edge_without_a_script_runner_just_leaves_the_party_where_it_is(seeded):
    owner, _, party = await crew(seeded, at=(0, 1))
    await walking.plan(seeded, RULES, owner.id, party.id, [(-1, 1)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, -1, 1)
    assert answer["edge"] == "west" and "dialog" not in answer


async def test_an_edge_that_has_gone_since_the_route_was_made_sends_the_party_back(seeded):
    owner, _, party = await crew(seeded, at=(0, 1))
    await walking.plan(seeded, RULES, owner.id, party.id, [(-1, 1)])
    await seeded.execute(MapObject.__table__.delete().where(MapObject.key == "edge-west"))
    answer = await walking.step(seeded, RULES, owner.id, party.id, -1, 1, run_edge(seeded))
    assert answer["confirmed"] is False and (answer["x"], answer["y"]) == (0, 1) and party.route is None


async def test_the_heroes_of_a_party_follow_it_as_it_walks(seeded):
    owner, hero, party = await crew(seeded, at=(0, 1))
    await walking.plan(seeded, RULES, owner.id, party.id, [(2, 1)])
    await walking.step(seeded, RULES, owner.id, party.id, 1, 1)
    await seeded.refresh(hero)
    assert (hero.x, hero.y) == (1, 1), "what a hero can reach is measured from where the party is"


# --- the calls -------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_the_calls_show_the_maps_objects_and_let_a_hero_use_them(app_client):
    mike = sign_in(app_client, "Mike")
    aria = expect(app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=mike), 201).json()["id"]

    async def stand_at(db, x, y, where="yard"):
        hero = await db.get(Hero, aria)
        hero.map_id, hero.x, hero.y = (await db.scalar(select(Map).where(Map.name == where))).id, x, y
        return (await on_map(db, "chest")).id, (await on_map(db, "door")).id, (await on_map(db, "edge-north")).id

    chest, door, edge = in_app_db(app_client, lambda db: stand_at(db, 3, 2))
    seen = expect(app_client.get("/api/maps/yard"), 200).json()
    assert [(each["key"], each["kind"], each["action"], each["x"], each["y"]) for each in seen["objects"]] == [
        ("chest", "chest", "open", 3, 1), ("door", "door", "open", 4, 2), ("sign", "sign", "read", 0, 0)]
    assert seen["edges"] == ["north", "west"] and "script" not in json.dumps(seen) and CHEST not in json.dumps(seen)
    assert expect(app_client.get("/api/maps/cellar"), 200).json()["objects"] == []

    listing = expect(app_client.get(f"/api/heroes/{aria}/nearby/open"), 200).json()
    assert [(each["kind"], each["key"]) for each in listing["nearby"]] == [("object", "door"), ("object", "chest")]

    url = f"/api/heroes/{aria}/objects/{chest}/use"
    expect(app_client.post(url), 403)  # no CSRF token
    expect(app_client.post(f"/api/heroes/{aria}/objects/{edge}/use", headers=mike), 404)
    expect(app_client.post(f"/api/heroes/{aria}/objects/9999/use", headers=mike), 404)
    expect(app_client.post(f"/api/heroes/{aria + 99}/objects/{chest}/use", headers=mike), 404)
    opened = expect(app_client.post(url, headers=mike), 200).json()
    assert opened["events"] == [{"type": "text", "text": "A potion!"}] and opened["window"] is False and opened["ended"]

    async def pack(db):
        return [(item.key, stack.qty) for stack, item in await inventory.stacks(db, await db.get(Hero, aria))]

    assert in_app_db(app_client, pack) == [("potion", 1)]

    in_app_db(app_client, lambda db: stand_at(db, 0, 0))
    assert "too far" in expect(app_client.post(url, headers=mike), 409).json()["detail"]

    in_app_db(app_client, lambda db: stand_at(db, 4, 1))
    asked = expect(app_client.post(f"/api/heroes/{aria}/objects/{door}/use", headers=mike), 200).json()
    assert asked["window"] is True and asked["prompt"]["accepts"]["choice"] == [0, 1]
    assert expect(app_client.get(f"/api/heroes/{aria}/dialog"), 200).json()["object"]["key"] == "door"
    gone = expect(app_client.post(f"/api/heroes/{aria}/dialog/next", json={"choice": 0}, headers=mike), 200).json()
    assert gone["ended"] is True

    async def place(db):
        hero = await db.get(Hero, aria, populate_existing=True)
        return (await db.get(Map, hero.map_id)).name, hero.x, hero.y

    assert in_app_db(app_client, place) == ("cellar", 1, 1)


def test_the_step_call_runs_an_edge_script(app_client):
    mike = sign_in(app_client, "Mike")

    aria = expect(app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=mike), 201).json()["id"]
    team = expect(app_client.post("/api/teams", json={"name": "Crew"}, headers=mike), 201).json()["id"]
    expect(app_client.post(f"/api/teams/{team}/add-hero", json={"hero_id": aria}, headers=mike), 200)
    expect(app_client.post(f"/api/teams/{team}/play", headers=mike), 200)

    async def place(db):
        party = await parties.party_of(db, team)
        party.map_id, party.x, party.y = (await db.scalar(select(Map).where(Map.name == "yard"))).id, 0, 1
        return party.id

    party = in_app_db(app_client, place)
    url = f"/api/parties/{party}/route"
    expect(app_client.post(url, json={"destination": {"x": -1, "y": 1}}, headers=mike), 200)
    answer = expect(app_client.post(f"{url}/step", json={"x": -1, "y": 1}, headers=mike), 200).json()
    assert answer["edge"] == "west" and answer["dialog"]["events"] == [{"type": "text", "text": "A wall of thorns."}]


# --- arriving in a town ----------------------------------------------------------------------------------------------

class CellarTown(Towns):
    """The cellar is a town; the yard is not."""

    async def is_town(self, session, map_id, x, y):
        return (await session.get(Map, map_id)).name == "cellar"


class DoorstepTown(Towns):
    """Only the tile in front of the yard's door is a town."""

    async def is_town(self, session, map_id, x, y):
        return (await session.get(Map, map_id)).name == "yard" and (x, y) == (3, 2)


async def test_a_party_that_walks_into_a_town_is_suspended_there_and_its_route_ends(seeded):
    owner, _, party = await crew(seeded, at=(1, 2))
    await walking.plan(seeded, RULES, owner.id, party.id, [(4, 2)])
    first = await walking.step(seeded, RULES, owner.id, party.id, 2, 2, town_hooks=DoorstepTown())
    assert first["confirmed"] and not first["done"] and "town" not in first and not await towns.is_suspended(seeded, party.id)
    second = await walking.step(seeded, RULES, owner.id, party.id, 3, 2, town_hooks=DoorstepTown())
    assert second["confirmed"] and second["done"] and second["town"] is True and (second["x"], second["y"]) == (3, 2)
    assert await towns.is_suspended(seeded, party.id) and (await seeded.get(Party, party.id)).route is None
    with pytest.raises(walking.WalkError, match="put back together"):
        await walking.plan(seeded, RULES, owner.id, party.id, [(4, 2)])


async def test_a_game_with_no_town_hooks_never_suspends_a_walking_party(seeded):
    owner, _, party = await crew(seeded, at=(2, 2))
    await walking.plan(seeded, RULES, owner.id, party.id, [(3, 2)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, 3, 2)
    assert answer["done"] and "town" not in answer and not await towns.is_suspended(seeded, party.id)


async def test_a_door_that_leads_to_a_town_suspends_the_party_once_the_dialog_has_run(seeded):
    _, hero, party = await crew(seeded, at=(4, 1))
    await objects.use(seeded, NPCS, REACH, hero, (await on_map(seeded, "door")).id)
    await npcs.answer(seeded, NPCS, REACH, hero, 0)
    assert not await towns.is_suspended(seeded, party.id), "the warp itself only moves the party"
    visit = await towns.settle_hero(seeded, CellarTown(), hero.id)
    assert visit is not None and await towns.is_suspended(seeded, party.id)
    assert await towns.settle_hero(seeded, CellarTown(), hero.id) is None, "settling twice changes nothing"


async def test_settling_a_hero_does_nothing_off_a_town_or_with_no_team(seeded):
    _, hero, party = await crew(seeded, at=(1, 1))
    assert await towns.settle_hero(seeded, CellarTown(), hero.id) is None and not await towns.is_suspended(seeded, party.id)
    owner = await create_account(seeded, "Solo", PASSWORD, email="solo@example.com", confirmed=True)
    alone = await heroes.create_hero(seeded, owner, "Solo-hero", "fighter")
    assert await towns.settle_hero(seeded, CellarTown(), alone.id) is None


async def test_a_party_in_a_fight_is_not_suspended_until_it_is_over(seeded, monkeypatch):
    _, hero, party = await crew(seeded, at=(1, 1))
    cellar = await seeded.scalar(select(Map).where(Map.name == "cellar"))
    await travel.relocate(seeded, party, cellar, 1, 1)

    async def busy(session, party):
        return True

    monkeypatch.setattr(towns, "in_fight", busy)
    assert await towns.settle_hero(seeded, CellarTown(), hero.id) is None and not await towns.is_suspended(seeded, party.id)
    monkeypatch.undo()
    assert await towns.settle_hero(seeded, CellarTown(), hero.id) is not None
