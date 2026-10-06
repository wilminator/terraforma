"""A hero's inventory, equipment, abilities and gold: stacking, wielding, ammunition, and who may touch what."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.schema import ContentError, check_seed
from terraforma.game import Game
from terraforma.heroes import inventory
from terraforma.heroes.models import Hero, HeroAbility, HeroEquipment, HeroItem
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect

PASSWORD = "correct horse battery"

SEED = {
    "abilities": [
        {"key": "slash", "name": "Slash", "kind": "skill"},
        {"key": "fire", "name": "Fire", "kind": "spell", "mp_cost": 3},
    ],
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": [
        {"ability": "slash", "level": 1}, {"ability": "fire", "level": 3}]}],
    "items": [
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "arrow", "name": "Arrow", "equip_slots": ["ammo"], "attack": {"ammo_type": "arrow"}},
        {"key": "bolt", "name": "Bolt", "equip_slots": ["ammo"], "attack": {"ammo_type": "bolt"}},
        {"key": "bow", "name": "Bow", "equip_slots": ["hand"], "attack": {"ammo_type": "arrow"}},
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"], "stat_bonus": {"Strength": 3}},
        {"key": "greatsword", "name": "Greatsword", "equip_slots": ["lhand", "rhand"]},
        {"key": "helm", "name": "Helm", "equip_slots": ["head"], "stat_bonus": {"Block": 2}, "stat_percent": {"Block": 10}},
    ],
}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


@pytest.fixture
def mike(app_client):
    headers = sign_in(app_client, "Mike")
    hero = testing.make_hero(app_client, headers, "Aria")
    return app_client, headers, hero["id"]


def give(client, hero_id, key, qty=1):
    """Hands the hero an item the way a game would (a shop or a drop): returns how many didn't fit."""
    async def work(db):
        return await inventory.add_item(db, await db.get(Hero, hero_id), key, qty)

    return in_app_db(client, work)


def look(client, hero_id):
    return expect(client.get(f"/api/heroes/{hero_id}/inventory"), 200).json()


def positions(client, hero_id):
    return {entry["item"]: entry["position"] for entry in look(client, hero_id)["items"]}


def equip(client, headers, hero_id, position, side=0):
    return client.post(f"/api/heroes/{hero_id}/equip", json={"position": position, "side": side}, headers=headers)


# --- abilities -----------------------------------------------------------------------------

def test_a_new_hero_knows_what_its_job_grants_at_level_one(mike):
    client, _headers, hero_id = mike
    assert [entry["key"] for entry in look(client, hero_id)["abilities"]] == ["slash"]


def test_a_level_up_teaches_more_and_learning_is_not_repeated(mike):
    client, _headers, hero_id = mike

    async def level(db):
        hero = await db.get(Hero, hero_id)
        hero.level = 3
        first = await inventory.grant_abilities(db, hero)
        again = await inventory.grant_abilities(db, hero)
        return first, again

    assert in_app_db(client, level) == (["Fire"], [])
    assert [entry["key"] for entry in look(client, hero_id)["abilities"]] == ["fire", "slash"], "in name order"


def test_job_abilities_may_be_bare_keys_or_have_levels():
    plain = check_seed({"abilities": SEED["abilities"], "jobs": [{"key": "j", "name": "J", "abilities": ["slash"]}]})
    assert (plain["jobs"][0].abilities[0].ability, plain["jobs"][0].abilities[0].level) == ("slash", 1)
    with pytest.raises(ContentError, match="level"):
        check_seed({"abilities": SEED["abilities"], "jobs": [{"key": "j", "name": "J", "abilities": [{"ability": "slash", "level": 0}]}]})
    with pytest.raises(ContentError, match="nope"):
        check_seed({"jobs": [{"key": "j", "name": "J", "abilities": [{"ability": "nope", "level": 2}]}]})


# --- the inventory -------------------------------------------------------------------------------

def test_things_that_are_not_gear_stack_up_to_the_limit(mike):
    client, _headers, hero_id = mike
    assert give(client, hero_id, "potion", 200) == 0
    assert give(client, hero_id, "potion", 100) == 0
    assert [(entry["item"], entry["qty"]) for entry in look(client, hero_id)["items"]] == [("potion", 250), ("potion", 50)]
    give(client, hero_id, "arrow", 10)
    give(client, hero_id, "arrow", 5)
    assert [entry["qty"] for entry in look(client, hero_id)["items"] if entry["item"] == "arrow"] == [15], "ammunition stacks"


def test_gear_never_stacks_and_a_full_inventory_hands_back_the_rest(mike, monkeypatch):
    client, _headers, hero_id = mike
    monkeypatch.setattr(inventory, "MAX_ITEMS", 3)
    assert give(client, hero_id, "sword", 2) == 0
    assert [entry["qty"] for entry in look(client, hero_id)["items"]] == [1, 1]
    assert give(client, hero_id, "sword", 3) == 2, "one more fits; two don't"
    assert give(client, hero_id, "potion", 1) == 1, "no room at all"
    assert len(look(client, hero_id)["items"]) == 3


def test_discarding_part_or_all_of_a_stack(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "potion", 10)
    give(client, hero_id, "sword")
    base = f"/api/heroes/{hero_id}/inventory/discard"
    assert expect(client.post(base, json={"position": 0, "qty": 4}, headers=headers), 200).json()["discarded"] == 4
    assert look(client, hero_id)["items"][0]["qty"] == 6
    answer = expect(client.post(base, json={"position": 0, "qty": 250}, headers=headers), 200).json()
    assert answer["discarded"] == 6 and [entry["item"] for entry in answer["items"]] == ["sword"]
    assert answer["items"][0]["position"] == 0, "positions close up"
    assert client.post(base, json={"position": 5}, headers=headers).status_code == 404


def test_discarding_something_worn_takes_it_off(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword")
    expect(equip(client, headers, hero_id, 0), 200)
    expect(client.post(f"/api/heroes/{hero_id}/inventory/discard", json={"position": 0}, headers=headers), 200)
    assert look(client, hero_id)["equipment"] == {}


def test_moving_a_stack_slides_the_others(mike):
    client, headers, hero_id = mike
    for key in ("potion", "sword", "helm", "bow"):
        give(client, hero_id, key)
    answer = expect(client.post(f"/api/heroes/{hero_id}/inventory/move", json={"from_position": 0, "to_position": 2}, headers=headers), 200)
    assert [entry["item"] for entry in answer.json()["items"]] == ["sword", "helm", "potion", "bow"]
    assert client.post(f"/api/heroes/{hero_id}/inventory/move", json={"from_position": 0, "to_position": 9}, headers=headers).status_code == 422


def test_what_is_worn_follows_its_stack_when_it_moves(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "potion")
    give(client, hero_id, "sword")
    expect(equip(client, headers, hero_id, 1), 200)
    expect(client.post(f"/api/heroes/{hero_id}/inventory/move", json={"from_position": 1, "to_position": 0}, headers=headers), 200)
    assert look(client, hero_id)["equipment"] == {"lhand": 0}


# --- equipment -----------------------------------------------------------------------------------

def test_a_weapon_goes_in_the_chosen_hand(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword", 2)
    assert expect(equip(client, headers, hero_id, 0, side=0), 200).json()["slots"] == ["lhand"]
    assert expect(equip(client, headers, hero_id, 1, side=1), 200).json()["slots"] == ["rhand"]
    assert look(client, hero_id)["equipment"] == {"lhand": 0, "rhand": 1}


def test_something_in_the_way_is_named_not_moved(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword", 2)
    equip(client, headers, hero_id, 0)
    answer = equip(client, headers, hero_id, 1, side=0)
    assert answer.status_code == 409 and answer.json() == {"outcome": "needs_unequipping", "occupying_position": 0}
    again = equip(client, headers, hero_id, 0, side=1)
    assert again.status_code == 409 and again.json()["occupying_position"] == 0, "already worn: take it off first"


def test_a_two_handed_weapon_needs_both_hands_free(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword")
    give(client, hero_id, "greatsword")
    equip(client, headers, hero_id, 0, side=1)
    assert equip(client, headers, hero_id, 1).status_code == 409
    expect(client.post(f"/api/heroes/{hero_id}/unequip", json={"position": 0}, headers=headers), 200)
    assert expect(equip(client, headers, hero_id, 1), 200).json()["slots"] == ["lhand", "rhand"]
    assert look(client, hero_id)["items"][1]["equipped_in"] == ["lhand", "rhand"]


def test_ammunition_needs_a_weapon_that_takes_it(mike):
    client, headers, hero_id = mike
    for key in ("arrow", "bolt", "bow", "sword"):
        give(client, hero_id, key, 5)
    where = positions(client, hero_id)
    answer = equip(client, headers, hero_id, where["arrow"])
    assert answer.status_code == 409 and answer.json() == {"outcome": "incompatible_ammo"}, "no weapon yet"
    equip(client, headers, hero_id, where["sword"])
    assert equip(client, headers, hero_id, where["arrow"]).status_code == 409, "a sword takes none"
    expect(client.post(f"/api/heroes/{hero_id}/unequip", json={"position": where["sword"]}, headers=headers), 200)
    equip(client, headers, hero_id, where["bow"])
    assert equip(client, headers, hero_id, where["bolt"]).status_code == 409, "wrong kind"
    assert expect(equip(client, headers, hero_id, where["arrow"]), 200).json()["slots"] == ["lammo"]


def test_putting_a_weapon_away_takes_its_ammunition_too(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "bow")
    give(client, hero_id, "arrow", 5)
    equip(client, headers, hero_id, 0, side=1)
    equip(client, headers, hero_id, 1, side=1)
    assert look(client, hero_id)["equipment"] == {"rammo": 1, "rhand": 0}
    answer = expect(client.post(f"/api/heroes/{hero_id}/unequip", json={"position": 0}, headers=headers), 200)
    assert sorted(answer.json()["slots"]) == ["rammo", "rhand"]
    assert look(client, hero_id)["equipment"] == {}


def test_other_things_cannot_be_worn_or_found(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "potion")
    answer = equip(client, headers, hero_id, 0)
    assert answer.status_code == 422 and answer.json() == {"outcome": "not_equipable"}
    assert equip(client, headers, hero_id, 7).status_code == 404
    assert client.post(f"/api/heroes/{hero_id}/unequip", json={"position": 7}, headers=headers).status_code == 404


def test_wearing_armour_in_its_slot(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "helm")
    assert expect(equip(client, headers, hero_id, 0), 200).json()["slots"] == ["head"]


# --- bonuses, ownership, cleanup -----------------------------------------------------------------------

def test_equipment_bonuses_add_percentages_first_then_flat_amounts():
    class Gear:
        def __init__(self, bonus=None, percent=None):
            self.stat_bonus, self.stat_percent = bonus or {}, percent or {}

    gear = [Gear({"Block": 2}, {"Block": 10}), Gear({"Block": 1}, {"Block": 10})]
    assert inventory.equipment_bonus(gear, "Block", 10) == 15, "10 * 1.2 + 3"
    assert inventory.equipment_bonus(gear, "Speed", 7) == 7
    assert [inventory.php_round(value) for value in (2.5, -2.5, 2.4, 0.5)] == [3, -3, 2, 1], "halves round away from zero"


def test_nobody_touches_another_accounts_hero_inventory(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword")
    other = sign_in(client, "Ann")
    assert client.get(f"/api/heroes/{hero_id}/inventory").status_code == 404
    assert equip(client, other, hero_id, 0).status_code == 404
    assert client.post(f"/api/heroes/{hero_id}/inventory/discard", json={"position": 0}, headers=other).status_code == 404


def test_the_inventory_calls_need_login_and_the_csrf_token(app_client):
    assert app_client.get("/api/heroes/1/inventory").status_code == 401
    headers = sign_in(app_client, "Mike")
    assert app_client.post("/api/heroes/1/equip", json={"position": 0}).status_code == 403
    answer = app_client.post("/api/heroes/1/equip", json={"position": 0, "extra": 1}, headers=headers)
    assert answer.status_code == 422, "unknown fields are refused"


def test_deleting_a_heros_team_removes_everything_the_hero_carried(mike):
    client, headers, hero_id = mike
    give(client, hero_id, "sword")
    equip(client, headers, hero_id, 0)
    team = expect(client.get("/api/teams"), 200).json()[0]["id"]
    expect(client.post(f"/api/teams/{team}/delete", headers=headers), 200)

    async def counts(db):
        return [await db.scalar(select(func.count()).select_from(table)) for table in (HeroItem, HeroEquipment, HeroAbility)]

    assert in_app_db(client, counts) == [0, 0, 0]
