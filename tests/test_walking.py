"""Walking: routes the server checks and fills in, steps it confirms or sends back, and the roll for monsters at the end of each."""

import json

import pytest
from sqlalchemy import select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.fights import store
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.maps import walking
from terraforma.maps.walking import find_path
from terraforma.models import Map, World
from terraforma.parties import service as parties
from terraforma.testing import in_app_db
from terraforma.world.rng import WorldRng

from .helpers import expect

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
RULES = Rules()

# 0 is a path, 1 a rock, 2 grass where a step always meets monsters.
FIELD = {
    "key": "field", "name": "The Field", "wrap_x": True, "safe_steps": 2,
    "tileset": [{"name": "path"}, {"name": "rock", "passable": False}, {"name": "grass", "encounter_rate": 10000}],
    "tiles": [[0, 0, 0, 0, 0],
              [0, 1, 1, 1, 0],
              [2, 2, 2, 2, 2]],
    "zones": [{"name": "wilds", "encounters": [{"monsters": ["slime"]}], "drops": ["herbs"]}],
}
# A strip of half-chance grass with no safe steps.
DUNES = {"key": "dunes", "name": "Dunes", "tileset": [{"encounter_rate": 5000}], "tiles": [[0] * 30],
         "zones": [{"encounters": [{"monsters": ["slime"]}]}]}
SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20, "MP": 2, "Speed": 5, "Strength": 5}}],
    "items": [{"key": "potion", "name": "Potion"}],
    "personalities": [{"key": "plain", "name": "Plain"}],
    "monsters": [{"key": "slime", "name": "Slime", "personality": "plain"}],
    "drop_tables": [{"key": "herbs", "name": "Herbs", "entries": [{"item": "potion", "chance": 5000}]}],
    "maps": [FIELD, DUNES],
}


class Solid(Rules):
    """Rules where parties can't walk through each other."""

    def party_blocks(self, mover, other) -> bool:
        return True


def grid(text: str) -> Map:
    """A map drawn in characters: # is a rock, anything else open."""
    rows = text.split()
    return Map(name="drawn", width=len(rows[0]), height=len(rows), tileset=[{"passable": True}, {"passable": False}],
               tiles=[[1 if each == "#" else 0 for each in row] for row in rows])


async def party_on(db, name, where="field", at=(0, 0), owner=None, seed=20261004):
    """A party of one team and one hero, standing on map $where at $at, in a world with a fixed seed."""
    if owner is None:
        owner = await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
    team = await heroes.create_team(db, owner, f"{name}'s team")
    hero = await heroes.create_hero(db, owner, f"{name}-hero", "fighter")
    await heroes.add_to_team(db, owner, team.id, hero.id)
    party = await parties.create_party(db, team.id, 20)
    found = await db.scalar(select(Map).where(Map.name == where))
    party.map_id, party.x, party.y = found.id, *at
    (await db.get(World, found.world_id)).seed = seed
    await db.flush()
    return owner, party


@pytest.fixture
async def seeded(db):
    await load_content(db, SEED)
    await db.commit()
    return db


# --- finding a way ----------------------------------------------------------------

def test_the_shortest_way_goes_round_a_rock():
    ground = grid("...\n.#.\n...")
    assert find_path(ground, (0, 1), (2, 1), set(), 10) == [(0, 0), (1, 0), (2, 0), (2, 1)]
    assert find_path(ground, (0, 0), (0, 0), set(), 10) == []
    assert find_path(ground, (0, 0), (1, 1), set(), 10) is None, "a rock can't be walked on"


def test_a_way_longer_than_the_limit_is_no_way():
    ground = grid("....")
    assert find_path(ground, (0, 0), (3, 0), set(), 3) == [(1, 0), (2, 0), (3, 0)]
    assert find_path(ground, (0, 0), (3, 0), set(), 2) is None


def test_a_blocked_tile_is_walked_round_and_a_wall_of_them_stops_the_way():
    ground = grid("...\n...")
    assert find_path(ground, (0, 0), (2, 0), {(1, 0)}, 10) == [(0, 1), (1, 1), (2, 1), (2, 0)]
    assert find_path(ground, (0, 0), (2, 0), {(1, 0), (1, 1)}, 10) is None


def test_the_way_goes_through_a_wrapped_edge_as_an_ordinary_step():
    ring = Map(name="ring", width=5, height=1, wrap_x=True)
    assert find_path(ring, (0, 0), (4, 0), set(), 10) == [(4, 0)]
    assert find_path(ring, (0, 0), (3, 0), set(), 10) == [(4, 0), (3, 0)]
    wall = Map(name="wall", width=5, height=1)
    assert find_path(wall, (0, 0), (4, 0), set(), 10) == [(1, 0), (2, 0), (3, 0), (4, 0)]


# --- routes -----------------------------------------------------------------------

async def test_a_destination_becomes_a_route_that_starts_where_the_party_stands(seeded):
    owner, party = await party_on(seeded, "Mike")
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    assert view["route"] == [[0, 0], [1, 0], [2, 0]] and view["at"] == 0 and view["map"] == "field" and view["revision"] == 1
    assert (party.x, party.y) == (0, 0), "planning moves nobody"
    assert (await walking.where(seeded, owner.id, party.id)) == view


async def test_a_proposed_path_has_its_gaps_filled_and_may_go_round_the_edge(seeded):
    owner, party = await party_on(seeded, "Mike")
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(0, 0), (4, 0), (4, 1), (4, 2), (3, 2)])
    assert view["route"] == [[0, 0], [4, 0], [4, 1], [4, 2], [3, 2]]
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0), (2, 2)])  # the rock is in between
    assert view["route"] == [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0], [4, 1], [4, 2], [3, 2], [2, 2]]
    assert view["route"] is not None and (await seeded.get(type(party), party.id)).route["tiles"] == view["route"], "the party keeps the route"


async def test_a_new_route_replaces_the_old_one(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    view = await walking.plan(seeded, RULES, owner.id, party.id, [(0, 2)])
    assert view["route"][-1] == [0, 2] and view["at"] == 0


@pytest.mark.parametrize("waypoints, message", [
    ([(1, 5)], "off the map"),
    ([(2, 0), (0, -1)], "off the map"),
    ([(2, 1)], "can't be walked on"),
    ([(0, 0)], "there already"),
])
async def test_a_route_the_map_does_not_allow_is_refused(seeded, waypoints, message):
    owner, party = await party_on(seeded, "Mike")
    with pytest.raises(walking.WalkError, match=message):
        await walking.plan(seeded, RULES, owner.id, party.id, waypoints)
    assert party.route is None


async def test_a_way_longer_than_the_games_limit_is_refused(seeded):
    class Short(Rules):
        route_limit = 3

    owner, party = await party_on(seeded, "Mike")
    with pytest.raises(walking.WalkError, match="3 steps or fewer"):
        await walking.plan(seeded, Short(), owner.id, party.id, [(2, 2)])
    assert (await walking.plan(seeded, Short(), owner.id, party.id, [(2, 0)]))["route"][-1] == [2, 0]


async def test_only_the_parties_leader_walks_it(seeded):
    owner, party = await party_on(seeded, "Mike")
    stranger = await create_account(seeded, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    with pytest.raises(walking.NotYours):
        await walking.plan(seeded, RULES, stranger.id, party.id, [(2, 0)])
    with pytest.raises(walking.NotYours):
        await walking.step(seeded, RULES, stranger.id, party.id, 1, 0)
    with pytest.raises(walking.NotFound):
        await walking.plan(seeded, RULES, owner.id, party.id + 99, [(2, 0)])


async def test_a_party_in_a_fight_does_not_walk(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    await live_fight(seeded, party)
    with pytest.raises(walking.WalkError, match="in a fight"):
        await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, 1, 0)
    assert answer["confirmed"] is False and "in a fight" in answer["reason"] and party.route is None


async def live_fight(db, party):
    from terraforma.fights import live

    return await live.start_party_fight(db, party.id, ["slime"], RULES)


# --- steps ------------------------------------------------------------------------

async def test_each_tile_of_the_route_is_confirmed_in_turn(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    first = await walking.step(seeded, RULES, owner.id, party.id, 1, 0)
    assert first == {"confirmed": True, "map": "field", "revision": 1, "x": 1, "y": 0, "done": False}
    assert (party.x, party.y, party.steps, party.walked, party.route["at"]) == (1, 0, 1, 1, 1)
    last = await walking.step(seeded, RULES, owner.id, party.id, 2, 0)
    assert last["confirmed"] and last["done"] and (party.x, party.y) == (2, 0) and party.route is None


async def test_a_tile_that_is_not_the_next_sends_the_party_back_and_ends_the_route(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(1, 0), (2, 0), (3, 0)])
    await walking.step(seeded, RULES, owner.id, party.id, 1, 0)
    for skipped in ((3, 0), (0, 0), (1, 1)):
        await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0), (3, 0)])
        answer = await walking.step(seeded, RULES, owner.id, party.id, *skipped)
        assert answer["confirmed"] is False and (answer["x"], answer["y"]) == (1, 0) and party.route is None
    assert (party.x, party.y) == (1, 0)
    again = await walking.step(seeded, RULES, owner.id, party.id, 2, 0)
    assert again["confirmed"] is False and "no route" in again["reason"]


async def test_a_map_that_changed_ends_the_route(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    (await seeded.get(Map, party.map_id)).revision += 1
    answer = await walking.step(seeded, RULES, owner.id, party.id, 1, 0)
    assert answer["confirmed"] is False and "changed" in answer["reason"] and answer["revision"] == 2 and (party.x, party.y) == (0, 0)


async def test_walking_through_a_wrapped_edge_counts_as_a_step(seeded):
    owner, party = await party_on(seeded, "Mike")
    await walking.plan(seeded, RULES, owner.id, party.id, [(4, 0)])
    answer = await walking.step(seeded, RULES, owner.id, party.id, 4, 0)
    assert answer["confirmed"] and answer["done"] and (party.x, party.y, party.steps) == (4, 0, 1)


async def test_a_party_that_other_parties_block_walks_round_them_and_is_stopped_if_one_moves_in(seeded):
    owner, party = await party_on(seeded, "Mike")
    _other_owner, other = await party_on(seeded, "Zed", at=(1, 0))
    free = await walking.plan(seeded, RULES, owner.id, party.id, [(2, 0)])
    assert free["route"] == [[0, 0], [1, 0], [2, 0]], "parties walk through each other unless the game says otherwise"
    solid = Solid()
    with pytest.raises(walking.WalkError, match="taken by another party"):
        await walking.plan(seeded, solid, owner.id, party.id, [(1, 0)])
    around = await walking.plan(seeded, solid, owner.id, party.id, [(2, 0)])
    assert around["route"] == [[0, 0], [4, 0], [3, 0], [2, 0]], "round the edge, past the party"
    other.x = 4
    answer = await walking.step(seeded, solid, owner.id, party.id, 4, 0)
    assert answer["confirmed"] is False and "in the way" in answer["reason"] and (party.x, party.y) == (0, 0)


# --- monsters ---------------------------------------------------------------------

async def walk_grass(db, owner, party, rules=RULES):
    """Walks the party along the grass of the field, one explicit tile at a time; returns each answer up to the first fight."""
    answers = []
    for x in (1, 2, 3):
        await walking.plan(db, rules, owner.id, party.id, [(x, 2)])
        answers.append(await walking.step(db, rules, owner.id, party.id, x, 2))
        if "fight" in answers[-1]:
            break
    return answers


async def test_the_safe_steps_pass_before_monsters_can_come(seeded):
    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    answers = await walk_grass(seeded, owner, party)
    assert ["fight" in each for each in answers] == [False, False, True], "the map allows 2 safe steps"
    hit = answers[-1]
    assert hit["monsters"] == ["slime"] and hit["done"] is True and party.route is None and (party.x, party.y) == (3, 2)
    record = await seeded.get(FightRecord, hit["fight"])
    assert (record.x, record.y) == (3, 2) and record.finished is False
    assert party.steps == 0, "the count starts over with the fight"


async def test_a_fight_drops_what_the_zone_it_is_in_drops(seeded):
    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    hit = (await walk_grass(seeded, owner, party))[-1]
    fight, _played = await store.load_state(seeded, await seeded.get(FightRecord, hit["fight"]), RULES)
    assert fight.area_drops == ["herbs"]


async def test_the_safe_steps_start_again_after_a_fight(seeded):
    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    hit = (await walk_grass(seeded, owner, party))[-1]
    record = await seeded.get(FightRecord, hit["fight"])
    record.finished = True
    await seeded.flush()
    answers = []
    for x in (2, 1, 0):  # back the way it came
        await walking.plan(seeded, RULES, owner.id, party.id, [(x, 2)])
        answers.append(await walking.step(seeded, RULES, owner.id, party.id, x, 2))
    assert ["fight" in each for each in answers] == [False, False, True], "two safe steps again"


async def test_a_fight_that_something_else_starts_also_restarts_the_count(seeded):
    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    await walking.plan(seeded, RULES, owner.id, party.id, [(1, 2)])
    await walking.step(seeded, RULES, owner.id, party.id, 1, 2)
    assert party.steps == 1
    await live_fight(seeded, party)
    assert party.steps == 0


async def test_no_encounter_where_the_tile_has_no_rate_or_the_zone_no_monsters(seeded):
    owner, party = await party_on(seeded, "Mike", at=(0, 0))
    field = await seeded.get(Map, party.map_id)
    field.safe_steps = 0
    for x in range(1, 5):
        await walking.plan(seeded, RULES, owner.id, party.id, [(x, 0)])
        assert "fight" not in await walking.step(seeded, RULES, owner.id, party.id, x, 0)
    field.zones = [{"name": "quiet", "encounters": [], "drops": [], "pvp": False}]
    await walking.plan(seeded, RULES, owner.id, party.id, [(4, 1), (4, 2)])
    await walking.step(seeded, RULES, owner.id, party.id, 4, 1)
    answer = await walking.step(seeded, RULES, owner.id, party.id, 4, 2)
    assert answer["confirmed"] and "fight" not in answer, "grass, but a zone with nothing to meet"


async def test_the_roll_at_each_step_comes_from_the_worlds_seed_and_nothing_else(seeded):
    seed, rate = 20261004, 5000
    owner, party = await party_on(seeded, "Mike", where="dunes", seed=seed)
    expected = next(n for n in range(1, 30) if WorldRng(seed).stream("encounter", party.id, n).randrange(10000) < rate)
    met = None
    for x in range(1, 30):
        await walking.plan(seeded, RULES, owner.id, party.id, [(x, 0)])
        answer = await walking.step(seeded, RULES, owner.id, party.id, x, 0)
        if "fight" in answer:
            met = x
            break
    assert met == expected, "the same seed meets monsters on the same step"


async def test_a_zones_encounters_are_picked_by_weight_from_the_ones_that_fit(seeded):
    class Small(Rules):
        party_size = 1

    owner, party = await party_on(seeded, "Mike", at=(0, 2))
    field = await seeded.get(Map, party.map_id)
    field.safe_steps = 0
    field.zones = [{"name": "wilds", "encounters": [{"monsters": ["slime", "slime"], "weight": 1000}, {"monsters": ["slime"], "weight": 1}], "drops": [], "pvp": False}]
    await walking.plan(seeded, Small(), owner.id, party.id, [(1, 2)])
    answer = await walking.step(seeded, Small(), owner.id, party.id, 1, 2)
    assert answer["monsters"] == ["slime"], "a pair of slimes doesn't fit a party size of 1"


# --- the calls --------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_the_calls_walk_a_party_and_refuse_the_wrong_requests(app_client):
    ids = {}

    async def setup(db):
        mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
        await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
        _, party = await party_on(db, "Aria", at=(0, 2), owner=mike)
        ids["party"] = party.id

    in_app_db(app_client, setup)
    token = app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    headers = {"X-CSRF-Token": token}
    url = f"/api/parties/{ids['party']}/route"

    seen = expect(app_client.get("/api/maps/field"), 200).json()
    assert (seen["width"], seen["height"], seen["revision"], seen["wrap_x"]) == (5, 3, 1, True)
    assert seen["tileset"][2] == {"name": "grass", "passable": True, "poison": False, "art": None}, "no encounter rates for the page"
    assert seen["zones"] == [{"name": "wilds", "pvp": False}] and seen["tiles"][2] == [2] * 5
    expect(app_client.get("/api/maps/nowhere"), 404)

    expect(app_client.post(url, json={"destination": {"x": 1, "y": 2}}), 403)  # no CSRF token
    expect(app_client.post(url, json={}, headers=headers), 422)
    expect(app_client.post(url, json={"path": [{"x": 1, "y": 2}], "destination": {"x": 1, "y": 2}}, headers=headers), 422)
    expect(app_client.post(url, json={"path": [], "extra": 1}, headers=headers), 422)
    expect(app_client.post(url, json={"destination": {"x": "1", "y": 2}}, headers=headers), 422)
    expect(app_client.post(f"/api/parties/{ids['party'] + 99}/route", json={"destination": {"x": 1, "y": 2}}, headers=headers), 404)
    assert "off the map" in expect(app_client.post(url, json={"destination": {"x": 1, "y": 9}}, headers=headers), 409).json()["detail"]

    route = expect(app_client.post(url, json={"path": [{"x": 3, "y": 2}]}, headers=headers), 200).json()
    assert route["route"] == [[0, 2], [4, 2], [3, 2]], "the shortest way is round the edge"
    assert expect(app_client.get(url), 200).json() == route
    step = f"{url}/step"
    expect(app_client.post(step, json={"x": 4, "y": 2}), 403)
    expect(app_client.post(step, json={"x": 4}, headers=headers), 422)
    wrong = expect(app_client.post(step, json={"x": 3, "y": 2}, headers=headers), 200).json()
    assert wrong["confirmed"] is False and (wrong["x"], wrong["y"]) == (0, 2)
    expect(app_client.post(url, json={"path": [{"x": 3, "y": 2}]}, headers=headers), 200)
    assert expect(app_client.post(step, json={"x": 4, "y": 2}, headers=headers), 200).json()["confirmed"] is True

    app_client.post("/api/logout", headers=headers)
    zed = app_client.post("/api/login", json={"username": "Zed", "password": PASSWORD}).json()["csrf_token"]
    expect(app_client.post(url, json={"destination": {"x": 1, "y": 2}}, headers={"X-CSRF-Token": zed}), 403)
    expect(app_client.get(url), 403)
