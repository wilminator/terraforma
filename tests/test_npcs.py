"""NPCs: standing on a map, who may talk to them from where, the conversation a hero is in, and the calls, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Hero
from terraforma.models import Account
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.reach.hooks import Reach
from terraforma.npcs.models import Npc, NpcTalk
from terraforma.npcs.script import ScriptError
from terraforma.parties import service as parties
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import a_team_fights_a_rat

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
NPCS = Npcs()
REACH = Reach()

KEEPER = ("Welcome to the Tavern.`ack`What can I do for you?`switch,Rooms,rooms,Chat,chat`\nWell?\n`jump,end`"
          "`label,rooms``inn,10,end`A room is 10 gold. Rest?`ack`Sleep well.`jump,end`"
          "`label,chat`Fine weather.")


async def a_hero(db, name="Aria", owner=None):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    owner = owner or await create_account(db, f"{name}Owner", PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
    return await heroes.create_hero(db, owner, name, "fighter")


async def keeper(db, hero, key="keeper", dialog=KEEPER, x=None, y=None, counter=((0, 1), (1, 1), (2, 1))):
    """The tavern keeper behind a counter on the row below the hero (hub tiles are all open)."""
    return await service.place_npc(db, key, "Keeper", hero.map_id, hero.x + 1 if x is None else x, hero.y + 2 if y is None else y, dialog, list(counter or []))


# --- standing on a map -----------------------------------------------------------------------------------------------

async def test_an_npc_stands_on_a_map_and_is_placed_again_without_a_second_row(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    again = await service.place_npc(db, "keeper", "Barkeep", hero.map_id, 5, 6, "Hi.", [(5, 7)])
    assert again.id == npc.id and (again.name, again.x, again.y, again.dialog, again.counter) == ("Barkeep", 5, 6, "Hi.", [[5, 7]])
    assert await db.scalar(select(func.count()).select_from(Npc)) == 1


async def test_a_dialog_that_does_not_check_is_not_placed(db):
    hero = await a_hero(db)
    with pytest.raises(ScriptError, match="unknown tag"):
        await keeper(db, hero, dialog="`dance`")
    assert await db.scalar(select(func.count()).select_from(Npc)) == 0
    with pytest.raises(service.NpcError, match="at most"):
        await keeper(db, hero, counter=[(n, 0) for n in range(service.MAX_COUNTER + 1)])


# --- who may talk from where -----------------------------------------------------------------------------------------

async def test_a_hero_talks_from_a_tile_next_to_the_counter_that_the_npc_is_also_next_to(db):
    hero = await a_hero(db)  # at (0, 0); the counter is the row y=1 at x=0..2, the keeper at (1, 2)
    npc = await keeper(db, hero)
    assert await REACH.npc(db, "talk", hero, npc) is None, "next to the counter tile (0, 1)"
    hero.x, hero.y = 1, 1
    assert await REACH.npc(db, "talk", hero, npc) is None, "on a counter tile"
    hero.x, hero.y = 3, 0
    assert await REACH.npc(db, "talk", hero, npc) is None, "diagonally next to the counter tile (2, 1)"
    hero.x, hero.y = 4, 1
    assert await REACH.npc(db, "talk", hero, npc) == "stand at the counter to talk", "two tiles from the counter's end"
    hero.x, hero.y = 1, 3
    assert await REACH.npc(db, "talk", hero, npc) == "stand at the counter to talk", "on the keeper's side, no counter between"
    npc.counter = []
    assert await REACH.npc(db, "talk", hero, npc) is None, "with no counter, anywhere next to the NPC"
    hero.x, hero.y = 9, 9
    assert await REACH.npc(db, "talk", hero, npc) == "that person is too far away"


async def test_an_npc_on_another_map_is_not_here(db):
    from terraforma.models import Map

    hero = await a_hero(db)
    npc = await keeper(db, hero)
    other = Map(world_id=(await db.get(Map, hero.map_id)).world_id, name="cellar")
    db.add(other)
    await db.flush()
    npc.map_id = other.id
    assert await REACH.npc(db, "talk", hero, npc) == "that person is not here"
    assert await service.npcs_here(db, REACH, hero) == []


async def test_the_people_a_hero_can_talk_to_are_listed_by_the_games_rule(db):
    class Locked(Reach):
        async def npc(self, session, action, actor, npc):
            return "the guard waves you off" if npc.key == "guard" else await super().npc(session, action, actor, npc)

    hero = await a_hero(db)
    await keeper(db, hero)
    await keeper(db, hero, key="guard", counter=())
    assert [npc.key for npc in await service.npcs_here(db, Locked(), hero)] == ["keeper"]
    with pytest.raises(service.NpcError, match="waves you off"):
        await service.talk(db, NPCS, Locked(), hero, (await db.scalar(select(Npc).where(Npc.key == "guard"))).id)


# --- a conversation --------------------------------------------------------------------------------------------------

async def test_a_conversation_runs_one_answer_at_a_time(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    first = await service.talk(db, NPCS, REACH, hero, npc.id)
    assert first["npc"] == {"id": npc.id, "key": "keeper", "name": "Keeper"}
    NEXT = {"choice": [], "next": True, "cancel": False}
    assert first["events"] == [{"type": "text", "text": "Welcome to the Tavern."}] and first["prompt"] == {"type": "ack", "accepts": NEXT} and not first["ended"]
    assert (await service.current(db, hero))["prompt"] == {"type": "ack", "accepts": NEXT}
    asked = await service.answer(db, NPCS, REACH, hero, None)
    assert asked["events"] == [{"type": "text", "text": "What can I do for you?\n"}] or asked["events"][0]["text"].startswith("What can I do")
    assert asked["prompt"] == {
        "type": "choice", "kind": "switch", "options": [{"text": "Rooms"}, {"text": "Chat"}], "accepts": {"choice": [0, 1], "next": False, "cancel": True},
    }, "where each answer goes stays on the server"
    inn = await service.answer(db, NPCS, REACH, hero, 0)
    assert inn["prompt"]["kind"] == "inn" and inn["events"][0]["text"] == "A room is 10 gold. Rest?"
    yes = await service.answer(db, NPCS, REACH, hero, 0)
    assert yes["prompt"] == {"type": "activity", "command": "inn", "parts": ["10", "end"], "accepts": NEXT}, "nothing rests the party until a game says how"
    slept = await service.answer(db, NPCS, REACH, hero, None)
    assert slept["prompt"] == {"type": "ack", "accepts": NEXT}, "with no label, the activity's end goes on with the text"
    done = await service.answer(db, NPCS, REACH, hero, None)
    assert done["events"] == [{"type": "text", "text": "Sleep well."}] and done["ended"] and done["prompt"] is None
    assert await service.current(db, hero) == {"talking": False}
    assert await db.scalar(select(func.count()).select_from(NpcTalk)) == 0


async def test_choosing_the_other_answer_goes_the_other_way(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    await service.talk(db, NPCS, REACH, hero, npc.id)
    await service.answer(db, NPCS, REACH, hero, None)
    chat = await service.answer(db, NPCS, REACH, hero, 1)
    assert chat["events"] == [{"type": "text", "text": "Fine weather."}] and chat["ended"]


async def test_a_wrong_answer_is_refused_and_the_conversation_stays_where_it_was(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    await service.talk(db, NPCS, REACH, hero, npc.id)
    with pytest.raises(service.NpcError, match="only Next"):
        await service.answer(db, NPCS, REACH, hero, 0)
    assert (await service.current(db, hero))["prompt"]["type"] == "ack", "only a broken text ends the talk, not a wrong answer"


async def test_talking_to_someone_else_leaves_the_first_conversation(db):
    hero = await a_hero(db)
    one = await keeper(db, hero)
    two = await keeper(db, hero, key="cook", dialog="`ack`Soup?")
    await service.talk(db, NPCS, REACH, hero, one.id)
    await service.talk(db, NPCS, REACH, hero, two.id)
    assert await db.scalar(select(func.count()).select_from(NpcTalk)) == 1
    assert (await service.current(db, hero))["npc"]["key"] == "cook"
    await service.leave(db, hero)
    assert await service.current(db, hero) == {"talking": False}


async def test_walking_away_ends_the_conversation(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    await service.talk(db, NPCS, REACH, hero, npc.id)
    hero.x, hero.y = 20, 20
    with pytest.raises(service.NpcError, match="counter"):
        await service.answer(db, NPCS, REACH, hero, None)
    assert await service.current(db, hero) == {"talking": False}


async def test_going_on_without_a_conversation_is_refused(db):
    hero = await a_hero(db)
    with pytest.raises(service.NpcError, match="not in a conversation"):
        await service.answer(db, NPCS, REACH, hero, None)
    with pytest.raises(service.NoSuchNpc):
        await service.talk(db, NPCS, REACH, hero, 999)


async def test_a_hero_in_a_fight_cannot_talk(db):
    hero, _record = await a_team_fights_a_rat(db)
    npc = await keeper(db, hero)
    with pytest.raises(service.NpcError, match="in a fight"):
        await service.talk(db, NPCS, REACH, hero, npc.id)


async def test_a_dialog_that_is_broken_after_it_was_placed_ends_the_talk_with_the_reason(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero, dialog="`label,a``jump,a`")
    with pytest.raises(service.NpcError, match="circles"):
        await service.talk(db, NPCS, REACH, hero, npc.id)
    assert await service.current(db, hero) == {"talking": False}


async def test_the_dialog_tags_see_the_players_teams_and_party(db):
    hero = await a_hero(db)
    account = await db.get(Account, hero.account_id)
    team = await heroes.create_team(db, account, "Vanguard")
    await heroes.add_to_team(db, account, team.id, hero.id)
    party = await parties.create_party(db, team.id, 20)
    who = await service.who_is(db, hero)
    assert who.team_ids == {team.id} and who.party_id == party.id
    npc = await keeper(db, hero, dialog=f"`team,{team.id},no`Hello, Vanguard.`jump,end``label,no`Who?")
    assert (await service.talk(db, NPCS, REACH, hero, npc.id))["events"] == [{"type": "text", "text": "Hello, Vanguard."}]
    stranger = await a_hero(db, "Zed")
    stranger.x, stranger.y = hero.x, hero.y
    assert (await service.talk(db, NPCS, REACH, stranger, npc.id))["events"] == [{"type": "text", "text": "Who?"}]


async def test_a_game_can_handle_the_tags_the_engine_leaves_to_it(db):
    class Healer(Npcs):
        async def tag(self, session, hero, command, parts):
            if command == "heal":
                hero.vitals = None
                return ""
            return None

    hero = await a_hero(db)
    hero.vitals = {"HP": 1}
    npc = await keeper(db, hero, dialog="`heal`You look better.")
    frame = await service.talk(db, Healer(), REACH, hero, npc.id)
    assert hero.vitals is None and frame["events"] == [{"type": "text", "text": "You look better."}] and frame["ended"]


async def test_deleting_a_hero_in_a_conversation_works(db):
    hero = await a_hero(db)
    npc = await keeper(db, hero)
    await service.talk(db, NPCS, REACH, hero, npc.id)
    await heroes.delete_hero(db, await db.get(Account, hero.account_id), hero.id)
    assert await db.scalar(select(func.count()).select_from(NpcTalk)) == 0 and await db.get(Hero, hero.id) is None


# --- the calls -------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


@pytest.fixture
def tavern(app_client):
    mike = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, mike, "Aria")["id"]

    async def set_up(db):
        return (await keeper(db, await db.get(Hero, aria))).id

    npc = in_app_db(app_client, set_up)
    zed = sign_in(app_client, "Zed")
    zara = testing.make_hero(app_client, zed, "Zara")["id"]
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    return app_client, mike, aria, zara, npc


def test_a_hero_talks_to_an_npc_over_http(tavern):
    client, mike, aria, _zara, npc = tavern
    assert expect(client.get(f"/api/heroes/{aria}/npcs"), 200).json() == [{"id": npc, "key": "keeper", "name": "Keeper"}]
    assert expect(client.get(f"/api/heroes/{aria}/dialog"), 200).json() == {"talking": False}
    first = expect(client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}, headers=mike), 200).json()
    assert first["prompt"]["type"] == "ack" and first["events"][0]["text"] == "Welcome to the Tavern."
    assert expect(client.get(f"/api/heroes/{aria}/dialog"), 200).json()["prompt"]["accepts"] == {"choice": [], "next": True, "cancel": False}
    asked = expect(client.post(f"/api/heroes/{aria}/dialog/next", json={}, headers=mike), 200).json()
    assert asked["prompt"]["options"] == [{"text": "Rooms"}, {"text": "Chat"}] and asked["prompt"]["accepts"]["choice"] == [0, 1]
    chat = expect(client.post(f"/api/heroes/{aria}/dialog/next", json={"choice": 1}, headers=mike), 200).json()
    assert chat["ended"] and chat["events"][0]["text"] == "Fine weather."
    expect(client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}, headers=mike), 200)
    assert expect(client.post(f"/api/heroes/{aria}/dialog/leave", json={}, headers=mike), 200).json() == {"talking": False}


def test_refusals_answer_409_404_and_the_wrong_player_is_turned_away(tavern):
    client, mike, aria, zara, npc = tavern
    assert client.post(f"/api/heroes/{aria}/dialog/next", json={}, headers=mike).status_code == 409, "not in a conversation"
    assert client.post(f"/api/heroes/{aria}/npcs/999/talk", json={}, headers=mike).status_code == 404

    async def walk_away(db):
        (await db.get(Hero, aria)).x = 30

    in_app_db(client, walk_away)
    assert expect(client.get(f"/api/heroes/{aria}/npcs"), 200).json() == []
    assert client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}, headers=mike).status_code == 409, "not at the counter"
    assert client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}).status_code == 403, "no CSRF token"
    assert client.post(f"/api/heroes/{zara}/npcs/{npc}/talk", json={}, headers=mike).status_code == 404, "not Mike's hero"
    client.post("/api/login", json={"username": "Zed", "password": PASSWORD})
    expect(client.get(f"/api/heroes/{aria}/dialog"), 404)  # Zed cannot look through Aria's eyes


def test_the_calls_take_only_what_they_define(tavern):
    client, mike, aria, _zara, npc = tavern
    expect(client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}, headers=mike), 200)
    expect(client.post(f"/api/heroes/{aria}/dialog/next", json={}, headers=mike), 200)
    for body in ({"choice": -1}, {"choice": "1"}, {"choice": 1, "extra": 1}, {"choice": 1.5}, {"choice": 10_000}):
        assert client.post(f"/api/heroes/{aria}/dialog/next", json=body, headers=mike).status_code == 422, body
    assert client.post(f"/api/heroes/{aria}/dialog/next", json={"choice": 7}, headers=mike).status_code == 409, "not one of the choices"
