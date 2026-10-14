"""Storage: the game's rule for where items are kept beside the packs, putting in, taking out, using from it, deliveries."""

import json

import pytest
from sqlalchemy import func, select

from terraforma import testing
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.game import Game
from terraforma.heroes import inventory
from terraforma.heroes.models import Hero
from terraforma.storage import service as storage_service
from terraforma.storage.hooks import AccountStorage, NoStorage, Storage, TeamStorage
from terraforma.storage.models import StorageItem
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import equip, give, look, sign_in

SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": []}],
    "items": [
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "herb", "name": "Herb"},
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"]},
    ],
}


def seed_dir(tmp_path):
    folder = tmp_path / "seed"
    folder.mkdir()
    for kind, content in SEED.items():
        (folder / f"{kind}.json").write_text(json.dumps(content))
    return folder


@pytest.fixture
def storage():
    """The game's storage rule; a test overrides this fixture to try another."""
    return TeamStorage()


@pytest.fixture
def game(tmp_path, storage):
    return Game(name="Test Game", seed_dir=seed_dir(tmp_path), storage=storage)


@pytest.fixture
def mike(app_client):
    """Aria and Bram on one team, Cade on another."""
    headers = sign_in(app_client, "Mike")
    team = testing.make_team(app_client, headers, "Alpha", heroes=("Aria", "Bram"))
    heroes = {entry["name"]: entry["id"] for entry in app_client.get("/api/heroes").json()}
    other = testing.make_hero(app_client, headers, "Cade")["id"]
    return app_client, headers, heroes["Aria"], heroes["Bram"], other, team["id"]


def stored(client, hero_id):
    return expect(client.get(f"/api/heroes/{hero_id}/storage"), 200).json()


def put(client, headers, hero_id, position, qty=1):
    return client.post(f"/api/heroes/{hero_id}/storage/put", json={"position": position, "qty": qty}, headers=headers)


def take(client, headers, hero_id, position, qty=1):
    return client.post(f"/api/heroes/{hero_id}/storage/take", json={"position": position, "qty": qty}, headers=headers)


def use(client, headers, hero_id, position, target=None):
    body = {"position": position} | ({"target_hero_id": target} if target else {})
    return client.post(f"/api/heroes/{hero_id}/storage/use", json=body, headers=headers)


def contents(client, hero_id):
    return [(entry["item"], entry["qty"]) for entry in stored(client, hero_id)["items"]]


def pack(client, hero_id):
    return [(entry["item"], entry["qty"]) for entry in look(client, hero_id)["items"]]


def hurt(client, hero_id):
    async def work(db):
        hero = await db.get(Hero, hero_id)
        hero.stats = {**hero.stats, "HP": 100, "MP": 50}
        hero.vitals = {"HP": 10, "MP": 50}

    in_app_db(client, work)


# --- no storage unless the game says so ---------------------------------------------------------------

@pytest.mark.parametrize("storage", [NoStorage()])
def test_a_game_without_storage_has_none(mike):
    client, headers, aria, *_ = mike
    assert stored(client, aria) == {"enabled": False, "owner": None, "capacity": 0, "items": []}
    give(client, aria, "herb")
    assert "no storage" in expect(put(client, headers, aria, 0), 409).json()["detail"]
    assert "no storage" in expect(take(client, headers, aria, 0), 409).json()["detail"]
    assert pack(client, aria) == [("herb", 1)]


# --- putting in and taking out ------------------------------------------------------------------------

def test_items_go_in_and_come_out_and_stack(mike):
    client, headers, aria, *_ = mike
    give(client, aria, "herb", 5)
    give(client, aria, "sword")
    answer = expect(put(client, headers, aria, 0, 3), 200).json()
    assert answer["owner"] == "team" and answer["capacity"] == 24
    assert pack(client, aria) == [("herb", 2), ("sword", 1)]
    expect(put(client, headers, aria, 0, 2), 200)
    assert contents(client, aria) == [("herb", 5)]
    assert pack(client, aria) == [("sword", 1)]  # the empty stack went and the rest slid up
    expect(put(client, headers, aria, 0), 200)
    assert contents(client, aria) == [("herb", 5), ("sword", 1)]
    expect(take(client, headers, aria, 0, 4), 200)
    assert contents(client, aria) == [("herb", 1), ("sword", 1)]
    assert pack(client, aria) == [("herb", 4)]
    expect(take(client, headers, aria, 0), 200)
    assert contents(client, aria) == [("sword", 1)]  # the last herb left, so the sword moved to position 0
    assert stored(client, aria)["items"][0]["position"] == 0


def test_a_teammate_shares_the_team_storage_and_another_team_does_not(mike):
    client, headers, aria, bram, cade, _team = mike
    give(client, aria, "herb", 2)
    expect(put(client, headers, aria, 0, 2), 200)
    assert contents(client, bram) == [("herb", 2)]
    expect(take(client, headers, bram, 0, 1), 200)
    assert pack(client, bram) == [("herb", 1)] and contents(client, aria) == [("herb", 1)]
    assert contents(client, cade) == []


def test_what_is_worn_what_is_missing_and_bad_counts_are_refused(mike):
    client, headers, aria, *_ = mike
    give(client, aria, "sword")
    give(client, aria, "herb", 2)
    expect(equip(client, headers, aria, 0), 200)
    assert "take it off first" in expect(put(client, headers, aria, 0), 409).json()["detail"]
    assert "nothing in that position" in expect(put(client, headers, aria, 7), 409).json()["detail"]
    assert "you have 2" in expect(put(client, headers, aria, 1, 3), 409).json()["detail"]
    assert "nothing in that position" in expect(take(client, headers, aria, 0), 409).json()["detail"]
    expect(put(client, headers, aria, 1, 2), 200)
    assert "storage has 2" in expect(take(client, headers, aria, 0, 3), 409).json()["detail"]
    assert pack(client, aria) == [("sword", 1)] and contents(client, aria) == [("herb", 2)]


class Small(TeamStorage):
    capacity = 2


@pytest.mark.parametrize("storage", [Small()])
def test_a_full_storage_and_a_full_pack_refuse_and_change_nothing(mike):
    client, headers, aria, *_ = mike
    for _ in range(3):
        give(client, aria, "sword")
    expect(put(client, headers, aria, 0), 200)
    expect(put(client, headers, aria, 0), 200)
    assert "no room" in expect(put(client, headers, aria, 0), 409).json()["detail"]
    assert pack(client, aria) == [("sword", 1)] and contents(client, aria) == [("sword", 1), ("sword", 1)]
    give(client, aria, "sword", inventory.MAX_ITEMS - 1)  # the pack is full: 12 stacks
    assert len(pack(client, aria)) == inventory.MAX_ITEMS
    assert "no room" in expect(take(client, headers, aria, 0), 409).json()["detail"]
    assert contents(client, aria) == [("sword", 1), ("sword", 1)]


# --- the game's rules ---------------------------------------------------------------------------------

class Picky(TeamStorage):
    async def reachable(self, session, hero):
        return None if hero.name != "Bram" else "storage is in town"

    async def may_put(self, session, hero, item):
        return "no swords" if item.key == "sword" else None

    async def may_take(self, session, hero, item):
        return "the herbs are locked" if item.key == "herb" else None


@pytest.mark.parametrize("storage", [Picky()])
def test_the_games_rules_can_refuse_where_what_and_who(mike):
    client, headers, aria, bram, *_ = mike
    give(client, aria, "sword")
    give(client, aria, "herb")
    assert "no swords" in expect(put(client, headers, aria, 0), 409).json()["detail"]
    expect(put(client, headers, aria, 1), 200)
    assert "locked" in expect(take(client, headers, aria, 0), 409).json()["detail"]
    assert "in town" in expect(put(client, headers, bram, 0), 409).json()["detail"]
    assert "in town" in expect(take(client, headers, bram, 0), 409).json()["detail"]
    assert contents(client, aria) == [("herb", 1)]


@pytest.mark.parametrize("storage", [AccountStorage()])
def test_account_storage_is_shared_by_every_hero_of_the_account(mike):
    client, headers, aria, _bram, cade, _team = mike
    give(client, aria, "herb")
    expect(put(client, headers, aria, 0), 200)
    assert contents(client, cade) == [("herb", 1)]
    assert stored(client, cade)["owner"] == "account"
    expect(take(client, headers, cade, 0), 200)
    assert pack(client, cade) == [("herb", 1)]


# --- fights ---------------------------------------------------------------------------------------------

def start_fight(client, hero_id):
    async def work(db):
        hero = await db.get(Hero, hero_id)
        record = FightRecord(guid="f" * 32, initial_state={}, map_id=hero.map_id, finished=False)
        db.add(record)
        await db.flush()
        db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=hero.name, hero_id=hero_id))

    in_app_db(client, work)


def test_nothing_moves_for_a_hero_in_a_running_fight(mike):
    client, headers, aria, bram, *_ = mike
    give(client, aria, "herb", 2)
    expect(put(client, headers, aria, 0), 200)
    start_fight(client, aria)
    assert "in a fight" in expect(put(client, headers, aria, 0), 409).json()["detail"]
    assert "in a fight" in expect(take(client, headers, aria, 0), 409).json()["detail"]
    assert "in a fight" in expect(use(client, headers, aria, 0), 409).json()["detail"]
    expect(take(client, headers, bram, 0), 200)  # a teammate who is not in it can


# --- using from storage ------------------------------------------------------------------------------------

def test_using_from_storage_is_refused_unless_the_game_allows_it(mike):
    client, headers, aria, *_ = mike
    give(client, aria, "potion")
    expect(put(client, headers, aria, 0), 200)
    hurt(client, aria)
    assert "take it out" in expect(use(client, headers, aria, 0), 409).json()["detail"]
    assert contents(client, aria) == [("potion", 1)]


class Cellar(TeamStorage):
    async def may_use(self, session, hero, item):
        return None


@pytest.mark.parametrize("storage", [Cellar()])
def test_using_from_storage_heals_and_uses_the_item_up(mike):
    client, headers, aria, bram, cade, _team = mike
    give(client, aria, "potion", 2)
    expect(put(client, headers, aria, 0, 2), 200)
    hurt(client, bram)
    answer = expect(use(client, headers, aria, 0, target=bram), 200).json()
    assert answer["after"]["HP"] > answer["before"]["HP"] and answer["used_up"] is True
    assert contents(client, aria) == [("potion", 1)]
    assert "nothing in that position" in expect(use(client, headers, aria, 4), 409).json()["detail"]
    expect(use(client, headers, aria, 0, target=cade), 422)  # Cade is at full HP: it would do nothing, so it is kept
    assert contents(client, aria) == [("potion", 1)]


# --- deliveries -------------------------------------------------------------------------------------------

def deliver(client, hero_id, key, qty, storage=None):
    async def work(db):
        hero = await db.get(Hero, hero_id)
        return await storage_service.deliver(db, storage or TeamStorage(), hero, key, qty)

    return in_app_db(client, work)


def test_a_delivery_fills_storage_then_the_next_hero_with_room(mike):
    client, headers, aria, bram, cade, _team = mike
    for _ in range(inventory.MAX_ITEMS):
        give(client, aria, "sword")  # Aria's pack is full
    result = deliver(client, aria, "sword", 3, Small())  # storage holds two
    assert (result.stored, result.packed, result.left) == (2, {bram: 1}, 0)
    assert contents(client, aria) == [("sword", 1), ("sword", 1)] and pack(client, bram) == [("sword", 1)]
    result = deliver(client, aria, "herb", 5)  # (24 stacks in storage by default; the herbs go there)
    assert (result.stored, result.packed, result.left) == (5, {}, 0)
    assert pack(client, cade) == []  # another team's heroes never get a share


def test_a_delivery_that_fits_nowhere_says_how_much_is_left(mike):
    client, headers, aria, bram, *_ = mike
    for hero in (aria, bram):
        for _ in range(inventory.MAX_ITEMS):
            give(client, hero, "sword")
    result = deliver(client, aria, "sword", 4, Small())
    assert (result.stored, result.packed, result.left) == (2, {}, 2)


@pytest.mark.parametrize("storage", [NoStorage()])
def test_a_delivery_without_storage_goes_to_the_packs_in_slot_order(mike):
    client, headers, aria, bram, *_ = mike
    for _ in range(inventory.MAX_ITEMS - 1):
        give(client, aria, "sword")
    result = deliver(client, aria, "sword", 3, NoStorage())
    assert (result.stored, result.packed, result.left) == (0, {aria: 1, bram: 2}, 0)


def test_a_delivery_asks_for_a_real_item_and_a_count(mike):
    client, headers, aria, *_ = mike
    with pytest.raises(storage_service.StorageError, match="no item"):
        deliver(client, aria, "nothing", 1)
    with pytest.raises(storage_service.StorageError, match="at least one"):
        deliver(client, aria, "herb", 0)


# --- a team that goes -------------------------------------------------------------------------------------

def test_deleting_a_team_drops_its_storage(mike):
    client, headers, aria, _bram, cade, team = mike
    give(client, aria, "herb")
    expect(put(client, headers, aria, 0), 200)
    expect(client.post(f"/api/teams/{team}/delete", headers=headers), 200)
    assert in_app_db(client, lambda db: db.scalar(select(func.count()).select_from(StorageItem))) == 0


# --- who may ask --------------------------------------------------------------------------------------------

def test_the_calls_need_login_the_csrf_token_and_the_heroes_owner(app_client, mike):
    client, headers, aria, *_ = mike
    give(client, aria, "herb")
    for path, body in (("put", {"position": 0}), ("take", {"position": 0}), ("use", {"position": 0})):
        assert client.post(f"/api/heroes/{aria}/storage/{path}", json=body).status_code == 403
        assert client.post(f"/api/heroes/{aria}/storage/{path}", json=body | {"extra": 1}, headers=headers).status_code == 422
    other = sign_in(client, "Zed")
    expect(client.get(f"/api/heroes/{aria}/storage"), 404)
    expect(put(client, other, aria, 0), 404)
    expect(take(client, other, aria, 0), 404)
    expect(use(client, other, aria, 0), 404)
    zed = testing.make_hero(client, other, "Zara")["id"]
    expect(use(client, other, zed, 0, target=aria), 404)
    client.cookies.clear()
    assert client.get(f"/api/heroes/{aria}/storage").status_code in (401, 403)
