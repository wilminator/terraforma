"""Talking to another party: what it offers (a fight, help or both) by the stance of the hero's team toward it, and that talking changes nothing."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Hero
from terraforma.models import Account
from terraforma.parties import service as parties
from terraforma.parties.models import Party
from terraforma.profiles import service as profiles
from terraforma.pvp.hooks import PvpZones
from terraforma.reach.hooks import Reach
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.models import Relationship
from terraforma.relations.service import apply as apply_relation
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import give, look
from .test_party_help import SEED, befriend, pair  # noqa: F401  (a fixture is used by being in the module)

pytestmark = pytest.mark.heroes


class Anywhere(PvpZones):
    async def allows_pvp(self, session, map_id, x, y):
        return True


class NoDuels(Rules):
    def may_challenge(self, stance: str) -> str | None:
        return "Not here." if stance == "enemy" else None


class Far(Reach):
    """Fights and talks reach nobody."""

    async def party(self, session, action, actor, party):
        return "out of reach"


@pytest.fixture
def game(tmp_path, request):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    extras = getattr(request, "param", None) or {}
    return Game(name="Test Game", seed_dir=seed_dir, pvp=Anywhere(), **extras)


def talk(client, hero_id, party_id, headers=None):
    return client.get(f"/api/heroes/{hero_id}/talk/party/{party_id}", headers=headers or {})


def options(answer):
    return {entry["action"]: entry for entry in answer["options"]}


def count_relationships(client):
    return in_app_db(client, lambda db: db.scalar(select(func.count()).select_from(Relationship)))


# --- what is offered ------------------------------------------------------------------------------------------

def test_an_enemy_party_offers_a_fight_and_no_help(pair):
    client, mike, aria, bram, teams = pair
    answer = expect(talk(client, aria, teams["bram"][1]), 200).json()
    assert answer["party_id"] == teams["bram"][1] and answer["stance"] == "enemy" and answer["teams"] == [], "a hidden team is not named"
    found = options(answer)
    assert found["fight"] == {"action": "fight", "available": True, "reason": None}
    assert found["help"]["available"] is False and "enemy" in found["help"]["reason"]


def test_a_friendly_party_offers_help_and_no_fight(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    answer = expect(talk(client, aria, teams["bram"][1]), 200).json()
    found = options(answer)
    assert answer["stance"] == "ally"
    assert found["help"] == {"action": "help", "available": True, "reason": None}
    assert found["fight"]["available"] is False and "allies" in found["fight"]["reason"]


def test_a_mixed_party_offers_both(pair):
    client, mike, aria, bram, teams = pair

    async def work(db):
        owner = await db.get(Account, (await db.get(Hero, bram)).account_id)
        team = await heroes.create_team(db, owner, "Second")
        second = await heroes.create_hero(db, owner, "Cleo", "fighter")
        await heroes.add_to_team(db, owner, team.id, second.id)
        await parties.join_party(db, teams["bram"][1], team.id, Rules().party_size)
        return team.id

    other = in_app_db(client, work)
    befriend(client, teams)  # Aria's team likes Bram's team and has not met the newcomers: both stances in one party
    in_app_db(client, lambda db: apply_relation(db, Relations(), Change(Ref("team", teams["aria"][0]), Ref("team", other), score=-80, by="game")))
    answer = expect(talk(client, aria, teams["bram"][1]), 200).json()
    found = options(answer)
    assert answer["stance"] == "neutral"
    assert found["fight"]["available"] is True and found["help"]["available"] is True


def test_visible_teams_are_named(pair):
    client, mike, aria, bram, teams = pair

    async def work(db):
        owner = await db.get(Account, (await db.get(Hero, bram)).account_id)
        await profiles.set_visible(db, owner, teams["bram"][0], True)

    in_app_db(client, work)
    assert [entry["id"] for entry in expect(talk(client, aria, teams["bram"][1]), 200).json()["teams"]] == [teams["bram"][0]]


# --- when not ----------------------------------------------------------------------------------------------------

def test_a_fight_is_not_offered_where_pvp_is_not_allowed(pair, monkeypatch):
    client, mike, aria, bram, teams = pair

    async def nowhere(self, session, map_id, x, y):
        return False

    monkeypatch.setattr(Anywhere, "allows_pvp", nowhere)
    found = options(expect(talk(client, aria, teams["bram"][1]), 200).json())
    assert found["fight"]["available"] is False and "not allowed here" in found["fight"]["reason"]


def test_a_party_out_of_reach_is_refused(pair):
    client, mike, aria, bram, teams = pair

    async def work(db):
        (await db.get(Party, teams["bram"][1])).x += 5

    in_app_db(client, work)
    assert "too far" in expect(talk(client, aria, teams["bram"][1]), 409).json()["detail"]


def test_your_own_party_and_a_missing_party_are_refused(pair):
    client, mike, aria, bram, teams = pair
    assert "your own" in expect(talk(client, aria, teams["aria"][1]), 409).json()["detail"]
    expect(talk(client, aria, 9999), 404)


# --- the games' say ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("game", [{"rules": NoDuels()}], indirect=True)
def test_a_game_decides_when_a_fight_is_offered(pair):
    client, mike, aria, bram, teams = pair
    found = options(expect(talk(client, aria, teams["bram"][1]), 200).json())
    assert found["fight"] == {"action": "fight", "available": False, "reason": "Not here."}


@pytest.mark.parametrize("game", [{"reach": Far()}], indirect=True)
def test_a_game_decides_who_is_in_reach(pair):
    client, mike, aria, bram, teams = pair
    assert expect(talk(client, aria, teams["bram"][1]), 409).json()["detail"] == "out of reach"


# --- it changes nothing --------------------------------------------------------------------------------------

def test_talking_forms_no_relationship_and_starts_nothing(pair):
    client, mike, aria, bram, teams = pair
    give(client, aria, "potion")
    before = count_relationships(client)
    expect(talk(client, aria, teams["bram"][1]), 200)
    assert count_relationships(client) == before
    assert look(client, aria)["items"][0]["qty"] == 1


def test_the_hero_must_be_the_callers_and_the_party_id_a_number(pair):
    client, mike, aria, bram, teams = pair
    expect(talk(client, bram, teams["aria"][1]), 404)  # Bram is Zed's
    expect(client.get(f"/api/heroes/{aria}/talk/party/0"), 422)
    expect(client.get(f"/api/heroes/{aria}/talk/party/x"), 422)
    client.post("/api/logout", headers=mike)
    expect(talk(client, aria, teams["bram"][1]), 401)
