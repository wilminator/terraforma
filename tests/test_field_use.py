"""Using an item outside a fight: healing, mana and revives on the caller's own heroes, and what refuses it."""

import json
import random

import pytest
from sqlalchemy import select

from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.fights.specs import EffectSpec
from terraforma.game import Game
from terraforma.heroes.models import Hero
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import give, look, sign_in

SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": []}],
    "items": [
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "tonic", "name": "Tonic", "use_effect": {"effect": "heal", "base": 10}},
        {"key": "ether", "name": "Ether", "one_use": True, "use_effect": {"effect": "restore_mp", "base": 20}},
        {"key": "phoenix", "name": "Phoenix Down", "one_use": True, "use_effect": {"effect": "revive", "base": 0, "added": 100}},
        {"key": "bomb", "name": "Bomb", "one_use": True, "use_effect": {"effect": "hurt", "base": 10}},
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"]},
    ],
}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def hurt(client, hero_id, **vitals):
    """Sets a hero's maximums (100 HP, 50 MP) and where its HP and MP stand now."""
    async def work(db):
        hero = await db.get(Hero, hero_id)
        hero.stats = {**hero.stats, "HP": 100, "MP": 50}
        hero.vitals = {"HP": 100, "MP": 50, **vitals}
    in_app_db(client, work)


def vitals(client, hero_id):
    return in_app_db(client, lambda db: _vitals(db, hero_id))


async def _vitals(db, hero_id):
    return (await db.get(Hero, hero_id)).vitals


@pytest.fixture
def mike(app_client):
    headers = sign_in(app_client, "Mike")
    first = expect(app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=headers), 201).json()["id"]
    second = expect(app_client.post("/api/heroes", json={"name": "Bram", "job": "fighter"}, headers=headers), 201).json()["id"]
    return app_client, headers, first, second


def use(client, headers, hero_id, position, target=None):
    body = {"position": position} | ({"target_hero_id": target} if target else {})
    return client.post(f"/api/heroes/{hero_id}/use-item", json=body, headers=headers)


# --- what it does --------------------------------------------------------------------------------------------

def test_a_potion_heals_the_hero_and_is_used_up(mike):
    client, headers, aria, _bram = mike
    hurt(client, aria, HP=20)
    give(client, aria, "potion", 2)
    answer = expect(use(client, headers, aria, 0), 200).json()
    assert answer == {"hero_id": aria, "before": {"HP": 20, "MP": 50}, "after": {"HP": 50, "MP": 50}, "used_up": True}
    assert vitals(client, aria) == {"HP": 50, "MP": 50}
    assert look(client, aria)["items"][0]["qty"] == 1


def test_healing_stops_at_the_maximum_and_a_full_hero_goes_back_to_nothing_stored(mike):
    client, headers, aria, _bram = mike
    hurt(client, aria, HP=90)
    give(client, aria, "potion")
    assert expect(use(client, headers, aria, 0), 200).json()["after"]["HP"] == 100
    assert vitals(client, aria) is None
    assert look(client, aria)["items"] == []


def test_an_ether_restores_mana_and_a_tonic_that_is_not_one_use_stays(mike):
    client, headers, aria, _bram = mike
    hurt(client, aria, HP=60, MP=10)
    give(client, aria, "ether")
    give(client, aria, "tonic")
    assert expect(use(client, headers, aria, 0), 200).json()["after"] == {"HP": 60, "MP": 30}
    positions = {entry["item"]: entry["position"] for entry in look(client, aria)["items"]}
    assert expect(use(client, headers, aria, positions["tonic"]), 200).json()["used_up"] is False
    assert [entry["item"] for entry in look(client, aria)["items"]] == ["tonic"]
    assert vitals(client, aria) == {"HP": 70, "MP": 30}


def test_a_revive_brings_back_a_dead_hero_and_another_of_the_callers_heroes_can_use_it(mike):
    client, headers, aria, bram = mike
    hurt(client, bram, HP=0, MP=0)
    give(client, aria, "phoenix")
    answer = expect(use(client, headers, aria, 0, target=bram), 200).json()
    assert answer["hero_id"] == bram and answer["after"]["HP"] == 100
    assert look(client, aria)["items"] == []


def test_a_games_own_rule_decides_what_the_field_allows():
    class NoRevives(Rules):
        def field_use(self, rng, effect, vitals, maximums):
            return None if effect.effect == "revive" else super().field_use(rng, effect, vitals, maximums)

    heal = EffectSpec("heal", 0, base=5, added=0)
    revive = EffectSpec("revive", 0, 0, 100)
    assert Rules().field_use(random.Random(1), heal, {"HP": 10, "MP": 0}, {"HP": 100, "MP": 5}) == {"HP": 15, "MP": 0}
    assert NoRevives().field_use(random.Random(1), revive, {"HP": 0, "MP": 0}, {"HP": 100, "MP": 5}) is None
    assert Rules().field_use(random.Random(1), revive, {"HP": 0, "MP": 0}, {"HP": 100, "MP": 5})["HP"] == 100
    assert Rules().field_use(random.Random(1), heal, {"HP": 0, "MP": 0}, {"HP": 100, "MP": 5}) == {"HP": 0, "MP": 0}


# --- what refuses it ---------------------------------------------------------------------------------------------

def test_a_use_that_would_do_nothing_is_refused_and_keeps_the_item(mike):
    client, headers, aria, bram = mike
    hurt(client, aria, HP=100, MP=50)
    hurt(client, bram, HP=0, MP=0)
    give(client, aria, "potion")
    assert "would do nothing" in expect(use(client, headers, aria, 0), 422).json()["detail"]
    assert "would do nothing" in expect(use(client, headers, aria, 0, target=bram), 422).json()["detail"]  # a potion can't revive
    assert look(client, aria)["items"][0]["qty"] == 1


def test_things_that_are_not_for_the_field_are_refused(mike):
    client, headers, aria, _bram = mike
    give(client, aria, "bomb")
    give(client, aria, "sword")
    assert "only be used in a fight" in expect(use(client, headers, aria, 0), 422).json()["detail"]
    assert "can't be used" in expect(use(client, headers, aria, 1), 422).json()["detail"]
    assert "nothing in that position" in expect(use(client, headers, aria, 5), 422).json()["detail"]
    assert [entry["item"] for entry in look(client, aria)["items"]] == ["bomb", "sword"]


def test_a_hero_in_a_running_fight_cannot_use_or_receive_but_a_finished_fight_does_not_matter(mike):
    client, headers, aria, bram = mike
    hurt(client, aria, HP=10)
    give(client, aria, "potion", 3)
    give(client, bram, "potion")

    async def fight(db, finished):
        hero = await db.get(Hero, bram)
        record = FightRecord(guid="f" * 32, initial_state={}, map_id=hero.map_id, finished=finished)
        db.add(record)
        await db.flush()
        db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name="Bram", hero_id=bram))

    in_app_db(client, lambda db: fight(db, False))
    assert "Bram is in a fight" in expect(use(client, headers, aria, 0, target=bram), 422).json()["detail"]
    assert "Bram is in a fight" in expect(use(client, headers, bram, 0), 422).json()["detail"]
    expect(use(client, headers, aria, 0), 200)  # Aria is not in it
    in_app_db(client, lambda db: _finish(db))
    hurt(client, bram, HP=10)
    expect(use(client, headers, aria, 0, target=bram), 200)


async def _finish(db):
    for record in (await db.scalars(select(FightRecord))).all():
        record.finished = True


def test_only_the_owner_can_use_it_and_it_needs_login_and_the_csrf_token(app_client, mike):
    client, headers, aria, _bram = mike
    hurt(client, aria, HP=10)
    give(client, aria, "potion")
    assert client.post(f"/api/heroes/{aria}/use-item", json={"position": 0}).status_code == 403
    assert client.post(f"/api/heroes/{aria}/use-item", json={"position": 0, "extra": 1}, headers=headers).status_code == 422
    other = sign_in(client, "Zed")
    expect(use(client, other, aria, 0), 404)
    zed = expect(client.post("/api/heroes", json={"name": "Zara", "job": "fighter"}, headers=other), 201).json()["id"]
    expect(use(client, other, zed, 0, target=aria), 404)
