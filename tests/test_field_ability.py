"""Casting an ability outside a fight: on the caller's own heroes, and as help to a hero of a party in reach. The cost comes out of the caster."""

import json

import pytest
from sqlalchemy import select

from terraforma import testing
from terraforma.content.models import Ability
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes.models import Hero, HeroAbility, TeamMember
from terraforma.parties import service as parties
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import sign_in
from .test_party_help import befriend, login

pytestmark = pytest.mark.heroes

SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": []}],
    "abilities": [
        {"key": "mend", "name": "Mend", "kind": "spell", "mp_cost": 10, "effect": {"effect": "heal", "targets": "group", "base": 30}},
        {"key": "focus", "name": "Focus", "kind": "skill", "mp_cost": 0, "effect": {"effect": "restore_mp", "base": 15}},
        {"key": "raise", "name": "Raise", "kind": "spell", "mp_cost": 20, "effect": {"effect": "revive", "base": 0, "added": 100}},
        {"key": "bolt", "name": "Bolt", "kind": "spell", "mp_cost": 5, "effect": {"effect": "hurt", "base": 10}},
    ],
}


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def teach(client, hero_id, *keys):
    async def work(db):
        for key in keys:
            ability = await db.scalar(select(Ability).where(Ability.key == key))
            db.add(HeroAbility(hero_id=hero_id, ability_id=ability.id))

    in_app_db(client, work)


def set_vitals(client, hero_id, **vitals):
    async def work(db):
        hero = await db.get(Hero, hero_id)
        hero.stats = {**hero.stats, "HP": 100, "MP": 50}
        hero.vitals = {"HP": 100, "MP": 50, **vitals}

    in_app_db(client, work)


def vitals(client, hero_id):
    async def work(db):
        return (await db.get(Hero, hero_id)).vitals

    return in_app_db(client, work)


@pytest.fixture
def mike(app_client):
    headers = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, headers, "Aria")["id"]
    bram = testing.make_hero(app_client, headers, "Bram")["id"]
    return app_client, headers, aria, bram


def cast(client, headers, hero_id, ability, target=None):
    body = {"ability": ability} | ({"target_hero_id": target} if target else {})
    return client.post(f"/api/heroes/{hero_id}/use-ability", json=body, headers=headers)


def test_a_spell_heals_another_of_the_callers_heroes_and_costs_the_caster(mike):
    client, headers, aria, bram = mike
    teach(client, aria, "mend")
    set_vitals(client, aria)
    set_vitals(client, bram, HP=20)
    answer = expect(cast(client, headers, aria, "mend", bram), 200).json()
    assert (answer["before"], answer["after"], answer["cost"], answer["ability"]) == ({"HP": 20, "MP": 50}, {"HP": 50, "MP": 50}, {"MP": 10}, "mend")
    assert vitals(client, bram) == {"HP": 50, "MP": 50}
    assert vitals(client, aria) == {"HP": 100, "MP": 40}, "only the caster pays"


def test_casting_on_oneself_pays_first_and_reports_the_state_before_the_cost(mike):
    client, headers, aria, _bram = mike
    teach(client, aria, "mend", "focus")
    set_vitals(client, aria, HP=60)
    answer = expect(cast(client, headers, aria, "mend"), 200).json()
    assert (answer["before"], answer["after"]) == ({"HP": 60, "MP": 50}, {"HP": 90, "MP": 40})
    assert vitals(client, aria) == {"HP": 90, "MP": 40}
    answer = expect(cast(client, headers, aria, "focus"), 200).json()
    assert answer["cost"] == {"MP": 0} and vitals(client, aria) == {"HP": 90, "MP": 50}, "a free skill still restores"


def test_a_revive_raises_a_fallen_hero(mike):
    client, headers, aria, bram = mike
    teach(client, aria, "raise")
    set_vitals(client, aria)
    set_vitals(client, bram, HP=0)
    expect(cast(client, headers, aria, "raise", bram), 200)
    assert vitals(client, bram) is None or vitals(client, bram)["HP"] == 100
    assert vitals(client, aria) == {"HP": 100, "MP": 30}


@pytest.mark.parametrize(
    ("ability", "setup", "message"),
    [
        ("mend", "unknown", "doesn't know"),
        ("bolt", "known", "only be used in a fight"),
        ("mend", "poor", "hasn't the MP"),
        ("mend", "full", "would do nothing"),
        ("mend", "fallen", "has fallen"),
    ],
)
def test_a_refused_cast_costs_nothing(mike, ability, setup, message):
    client, headers, aria, bram = mike
    if setup != "unknown":
        teach(client, aria, "mend", "bolt")
    set_vitals(client, aria, **({"MP": 5} if setup == "poor" else {"HP": 0} if setup == "fallen" else {}))
    set_vitals(client, bram, **({} if setup == "full" else {"HP": 20}))
    before = vitals(client, aria)
    assert message in expect(cast(client, headers, aria, ability, bram), 422).json()["detail"]
    assert vitals(client, aria) == before and (setup == "full" or vitals(client, bram)["HP"] == 20)


def test_another_players_hero_is_not_a_target_of_the_own_hero_call(app_client):
    mike = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, mike, "Aria")["id"]
    zed = sign_in(app_client, "Zed")
    bram = testing.make_hero(app_client, zed, "Bram")["id"]
    mike = login(app_client, "Mike")
    teach(app_client, aria, "mend")
    assert "no such hero" in expect(cast(app_client, mike, aria, "mend", bram), 404).json()["detail"]
    assert vitals(app_client, aria) is None, "nothing was paid"


# --- as help to a party in reach ----------------------------------------------------------------------------

@pytest.fixture
def pair(app_client):
    """Mike's Aria and Zed's Bram on one tile, each in a party of their own; Bram is hurt."""
    mike = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, mike, "Aria")["id"]
    zed = sign_in(app_client, "Zed")
    bram = testing.make_hero(app_client, zed, "Bram")["id"]
    mike = login(app_client, "Mike")

    async def work(db):
        result = {}
        for name, hero_id in (("aria", aria), ("bram", bram)):
            team_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id))
            result[name] = (team_id, (await parties.create_party(db, team_id, Rules().party_size)).id)
        return result

    teams = in_app_db(app_client, work)
    teach(app_client, aria, "mend")
    set_vitals(app_client, aria)
    set_vitals(app_client, bram, HP=20)
    return app_client, mike, aria, bram, teams


def help_cast(client, headers, hero_id, party_id, ability="mend", member=0):
    return client.post(f"/api/heroes/{hero_id}/help/use-ability", json={"ability": ability, "party_id": party_id, "member": member}, headers=headers)


def test_a_friend_in_reach_is_healed_and_the_caster_pays(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    answer = expect(help_cast(client, mike, aria, teams["bram"][1]), 200).json()
    assert answer == {"party_id": teams["bram"][1], "member": 0, "before": {"HP": 20, "MP": 50}, "after": {"HP": 50, "MP": 50}, "cost": {"MP": 10}, "ability": "mend"}
    assert vitals(client, bram) == {"HP": 50, "MP": 50} and vitals(client, aria) == {"HP": 100, "MP": 40}


def test_a_stranger_is_an_enemy_and_is_not_helped_and_nothing_is_spent(pair):
    client, mike, aria, bram, teams = pair
    assert "enemy" in expect(help_cast(client, mike, aria, teams["bram"][1]), 422).json()["detail"]
    assert vitals(client, bram)["HP"] == 20 and vitals(client, aria) == {"HP": 100, "MP": 50}


def test_a_member_that_is_not_in_the_party_is_refused(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    assert "no such member" in expect(help_cast(client, mike, aria, teams["bram"][1], member=5), 422).json()["detail"]
