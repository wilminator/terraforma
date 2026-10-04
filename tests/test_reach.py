"""Reach: the game's rule for who a hero can reach for an action, and the nearby list built from it (NPCs, then parties, nearest
first), on every database."""

import json

import pytest

from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.models import Account, Map
from terraforma.profiles import service as profiles
from terraforma.reach import service
from terraforma.reach.hooks import ACTIONS, FIGHT, INVITE, OPEN, SEARCH, TALK, Reach, distance
from terraforma.parties import service as parties
from terraforma.testing import in_app_db
from terraforma.towns import service as towns
from terraforma.towns.hooks import Towns

from .helpers import expect
from .test_npcs import SEED, a_hero, keeper, sign_in

pytestmark = pytest.mark.anyio

REACH, RULES = Reach(), Rules()


async def a_party(db, name, x, y, map_id=None, visible=False):
    """A hero of its own player, alone on a team in a party, standing at (x, y)."""
    hero = await a_hero(db, name)
    hero.x, hero.y = x, y
    if map_id is not None:
        hero.map_id = map_id
    account = await db.get(Account, hero.account_id)
    team = await heroes.create_team(db, account, f"{name}Team")
    await heroes.add_to_team(db, account, team.id, hero.id)
    party = await parties.create_party(db, team.id, RULES.party_size)
    if visible:
        await profiles.set_visible(db, account, team.id, True)
    return hero, team, party


def keys(listing):
    return [(entry["kind"], entry.get("key") or entry["id"]) for entry in listing["nearby"]]


# --- distance --------------------------------------------------------------------------------------------------------

def test_distance_counts_a_diagonal_step_as_one_and_goes_round_a_wrapped_edge():
    flat = Map(width=10, height=10)
    assert distance(None, (0, 0), (3, 4)) == 4 and distance(flat, (0, 0), (9, 0)) == 9
    ring = Map(width=10, height=10, wrap_x=True)
    assert distance(ring, (0, 0), (9, 0)) == 1, "round the wrapped edge"
    assert distance(ring, (0, 0), (0, 9)) == 9, "the other axis does not wrap"
    assert distance(Map(width=10, height=10, wrap_x=True, wrap_y=True), (0, 0), (9, 9)) == 1


# --- the rules --------------------------------------------------------------------------------------------------------

async def test_an_npc_is_reached_for_a_talk_by_the_counter_and_for_any_other_action_from_next_to_it(db):
    hero = await a_hero(db)  # at (0, 0); the keeper stands at (1, 2) behind a counter on the row y=1
    npc = await keeper(db, hero)
    assert await REACH.npc(db, TALK, hero, npc) is None
    assert await REACH.npc(db, OPEN, hero, npc) == "that person is too far away", "two tiles from the keeper"
    hero.x, hero.y = 1, 1
    assert await REACH.npc(db, SEARCH, hero, npc) is None, "next to the keeper"
    hero.x, hero.y = 9, 9
    assert await REACH.npc(db, TALK, hero, npc) == "stand at the counter to talk"


async def test_a_party_is_reached_from_one_tile_on_its_map(db):
    hero, _team, _party = await a_party(db, "Aria", 0, 0)
    _other, _team, near = await a_party(db, "Bo", 1, 1)
    _other, _team, far = await a_party(db, "Cy", 2, 0)
    assert await REACH.party(db, FIGHT, hero, near) is None
    assert await REACH.party(db, FIGHT, hero, far) == "that party is too far away"
    other_map = Map(world_id=(await db.get(Map, hero.map_id)).world_id, name="cellar")
    db.add(other_map)
    await db.flush()
    near.map_id = other_map.id
    assert await REACH.party(db, FIGHT, hero, near) == "that party is not here"
    assert await REACH.party(db, FIGHT, near, far) == "that party is not here", "a party can be the one reaching"


def test_the_engines_actions_are_named_and_the_lists_stay_valid_for_the_action():
    assert ACTIONS == ("talk", "invite", "open", "search", "fight", "help")
    assert (REACH.valid_for(TALK), REACH.valid_for(INVITE), REACH.valid_for(SEARCH)) == (60, 300, 60)

    class Slow(Reach):
        valid_seconds = {TALK: 5}

    assert (Slow().valid_for(TALK), Slow().valid_for(INVITE)) == (5, 60)


# --- the nearby list -------------------------------------------------------------------------------------------------

async def test_a_talk_lists_the_npcs_then_the_parties_each_nearest_first(db):
    hero, _team, mine = await a_party(db, "Aria", 0, 0)
    await keeper(db, hero)  # (1, 2): two away
    await keeper(db, hero, key="guard", x=1, y=0, counter=())  # one away
    _h, _t, same_tile = await a_party(db, "Bo", 0, 0)
    _h, _t, diagonal = await a_party(db, "Cy", 1, 1)
    _h, _t, far = await a_party(db, "Di", 5, 5)
    _h, _t, in_town = await a_party(db, "Ed", 1, 0)
    await towns.enter_town(db, Towns(), in_town.id)
    listing = await service.nearby(db, REACH, RULES, hero, TALK)
    assert keys(listing) == [("npc", "guard"), ("npc", "keeper"), ("party", same_tile.id), ("party", diagonal.id)]
    assert [entry["distance"] for entry in listing["nearby"]] == [1, 2, 0, 1]
    assert (listing["action"], listing["valid_for"]) == ("talk", 60)
    parties_listed = [entry["id"] for entry in listing["nearby"] if entry["kind"] == "party"]
    assert mine.id not in parties_listed, "not the hero's own party"
    assert far.id not in parties_listed, "five tiles away"
    assert in_town.id not in parties_listed, "a party in a town is suspended, not on the map" 


async def test_a_hidden_team_is_just_part_of_a_party_and_a_visible_one_is_named(db):
    hero, _team, _mine = await a_party(db, "Aria", 0, 0)
    _h, shown, shown_party = await a_party(db, "Bo", 1, 0, visible=True)
    _h, _hidden, hidden_party = await a_party(db, "Cy", 0, 1)
    listing = {entry["id"]: entry for entry in (await service.nearby(db, REACH, RULES, hero, TALK))["nearby"]}
    assert listing[shown_party.id]["teams"] == [{"id": shown.id, "name": "BoTeam"}]
    assert listing[hidden_party.id]["teams"] == []


async def test_the_action_decides_what_is_listed(db):
    hero, _team, _mine = await a_party(db, "Aria", 0, 0)
    await keeper(db, hero, key="guard", x=1, y=0, counter=())
    await keeper(db, hero, key="far", x=1, y=3, counter=())
    _h, _t, party = await a_party(db, "Bo", 1, 1)
    assert keys(await service.nearby(db, REACH, RULES, hero, TALK)) == [("npc", "guard"), ("party", party.id)]
    assert keys(await service.nearby(db, REACH, RULES, hero, INVITE)) == [("npc", "guard"), ("party", party.id)]
    assert keys(await service.nearby(db, REACH, RULES, hero, OPEN)) == [("npc", "guard")], "a chest is opened, not talked to"
    assert (await service.nearby(db, REACH, RULES, hero, INVITE))["valid_for"] == 300


async def test_the_list_is_capped_by_the_rules_with_the_npcs_first(db):
    class Few(Rules):
        nearby_limit = 2

    hero, _team, _mine = await a_party(db, "Aria", 0, 0)
    await keeper(db, hero, key="guard", x=1, y=0, counter=())
    await keeper(db, hero, key="cook", x=0, y=1, counter=())
    await a_party(db, "Bo", 0, 0)
    assert keys(await service.nearby(db, REACH, Few(), hero, TALK)) == [("npc", "guard"), ("npc", "cook")]


async def test_a_game_sets_the_party_range_apart_from_the_npc_range(db):
    class Wide(Reach):
        party_window = 3

        async def party(self, session, action, actor, party):
            return None if distance(await session.get(Map, party.map_id), (actor.x, actor.y), (party.x, party.y)) <= 3 else "too far"

    class Everywhere(Wide):
        party_window = None

    class Quiet(Reach):
        async def lists_parties(self, session, action, hero):
            return False

    hero, _team, _mine = await a_party(db, "Aria", 0, 0)
    await keeper(db, hero, key="guard", x=1, y=0, counter=())
    _h, _t, three = await a_party(db, "Bo", 3, 0)
    _h, _t, five = await a_party(db, "Cy", 5, 5)
    assert keys(await service.nearby(db, REACH, RULES, hero, TALK)) == [("npc", "guard")], "the default reaches one tile"
    assert keys(await service.nearby(db, Wide(), RULES, hero, TALK)) == [("npc", "guard"), ("party", three.id)]
    assert keys(await service.nearby(db, Everywhere(), RULES, hero, TALK)) == [("npc", "guard"), ("party", three.id)], "asked about every party, the rule says no to the far one"
    assert keys(await service.nearby(db, Quiet(), RULES, hero, TALK)) == [("npc", "guard")]
    assert five.id != three.id


async def test_a_party_across_a_wrapped_edge_is_near(db):
    hero, _team, _mine = await a_party(db, "Aria", 0, 0)
    ring = Map(world_id=(await db.get(Map, hero.map_id)).world_id, name="ring", width=10, height=10, wrap_x=True)
    db.add(ring)
    await db.flush()
    hero.map_id = ring.id
    _h, _t, over_the_edge = await a_party(db, "Bo", 9, 0, map_id=ring.id)
    _h, _t, two_away = await a_party(db, "Cy", 8, 0, map_id=ring.id)
    listing = await service.nearby(db, REACH, RULES, hero, TALK)
    assert keys(listing) == [("party", over_the_edge.id)] and listing["nearby"][0]["distance"] == 1
    ring.wrap_x = False
    assert keys(await service.nearby(db, REACH, RULES, hero, TALK)) == []
    assert two_away.id != over_the_edge.id


async def test_a_hero_with_no_party_still_sees_the_parties_near(db):
    hero = await a_hero(db)
    _h, _t, party = await a_party(db, "Bo", 0, 0)
    assert keys(await service.nearby(db, REACH, RULES, hero, TALK)) == [("party", party.id)]


# --- the call ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_the_nearby_call_lists_what_is_in_reach(app_client):
    expect(app_client.get("/api/heroes/1/nearby/talk"), 401)
    mike = sign_in(app_client, "Mike")
    aria = expect(app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=mike), 201).json()["id"]
    npc = in_app_db(app_client, lambda db: _place(db, aria))
    assert expect(app_client.get(f"/api/heroes/{aria}/nearby/talk"), 200).json() == {
        "action": "talk", "valid_for": 60, "nearby": [{"kind": "npc", "id": npc, "key": "keeper", "name": "Keeper", "distance": 2}],
    }
    assert expect(app_client.get(f"/api/heroes/{aria}/nearby/invite"), 200).json()["valid_for"] == 300
    assert expect(app_client.get(f"/api/heroes/{aria}/nearby/open"), 200).json()["nearby"] == [], "the keeper is two tiles away"
    for action in ("Talk", "9", "talk-to", "x" * 40):
        expect(app_client.get(f"/api/heroes/{aria}/nearby/{action}"), 422)
    sign_in(app_client, "Zed")
    expect(app_client.get(f"/api/heroes/{aria}/nearby/talk"), 404)


async def _place(db, hero_id):
    from terraforma.heroes.models import Hero

    return (await keeper(db, await db.get(Hero, hero_id))).id
