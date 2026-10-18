"""Helping another party: an item used on a hero of a party in reach, unless the hero's team counts that party as an enemy."""

import json

import pytest

from terraforma.accounts import ratelimit
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes.models import Hero, TeamMember
from terraforma.parties import service as parties
from terraforma.parties.models import Party
from terraforma.reach.hooks import HELP, TALK, Reach
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.service import apply as apply_relation
from sqlalchemy import select
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import PASSWORD, give, look, sign_in

pytestmark = pytest.mark.heroes

SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": []}],
    "items": [
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"]},
    ],
}


class NoComfort(Rules):
    def may_help(self, stance: str) -> str | None:
        return "Not today." if stance == "ally" else None


@pytest.fixture
def game(tmp_path, request):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, rules=getattr(request, "param", None) or Rules())


@pytest.fixture
def pair(app_client):
    """Mike's Aria and Zed's Bram, each alone on a team in a party of its own, on the same tile and friends (Mike's team likes Zed's)."""
    mike = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, mike, "Aria")["id"]
    zed = sign_in(app_client, "Zed")  # (logs Zed in, in Mike's place)
    bram = testing.make_hero(app_client, zed, "Bram")["id"]
    mike = login(app_client, "Mike")

    async def work(db):
        result = {}
        for name, hero_id in (("aria", aria), ("bram", bram)):
            team_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id))
            party = await parties.create_party(db, team_id, Rules().party_size)
            result[name] = (team_id, party.id)
        hero = await db.get(Hero, bram)
        hero.stats = {**hero.stats, "HP": 100, "MP": 50}
        hero.vitals = {"HP": 20, "MP": 50}
        return result

    teams = in_app_db(app_client, work)
    return app_client, mike, aria, bram, teams


def login(client, username):
    """Logs a player who has an account in again: the CSRF header."""
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def befriend(client, teams):
    async def work(db):
        await apply_relation(db, Relations(), Change(Ref("team", teams["aria"][0]), Ref("team", teams["bram"][0]), score=50, by="game"))

    in_app_db(client, work)


def help_with(client, headers, hero_id, party_id, member=0, position=0):
    return client.post(f"/api/heroes/{hero_id}/help/use-item", json={"position": position, "party_id": party_id, "member": member}, headers=headers)


def bram_vitals(client, bram):
    async def work(db):
        return (await db.get(Hero, bram)).vitals

    return in_app_db(client, work)


# --- what it does ----------------------------------------------------------------------------------------------

def test_a_friend_in_reach_is_healed_with_the_helpers_potion(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion", 2)
    answer = expect(help_with(client, mike, aria, teams["bram"][1]), 200).json()
    assert answer == {"party_id": teams["bram"][1], "member": 0, "before": {"HP": 20, "MP": 50}, "after": {"HP": 50, "MP": 50}, "used_up": True}
    assert "hero_id" not in answer, "the player never learns the id of a hero in a team they may not see"
    assert bram_vitals(client, bram) == {"HP": 50, "MP": 50}
    assert look(client, aria)["items"][0]["qty"] == 1, "the helper's potion was spent"


def test_the_other_player_is_not_asked_and_can_be_helped_again(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion", 2)
    expect(help_with(client, mike, aria, teams["bram"][1]), 200)
    assert expect(help_with(client, mike, aria, teams["bram"][1]), 200).json()["after"]["HP"] == 80


def test_a_stranger_counts_as_an_enemy_and_is_not_helped(pair):
    client, mike, aria, bram, teams = pair
    give(client, aria, "potion")
    assert "enemy" in expect(help_with(client, mike, aria, teams["bram"][1]), 422).json()["detail"]
    assert bram_vitals(client, bram) == {"HP": 20, "MP": 50}
    assert look(client, aria)["items"][0]["qty"] == 1, "a refused help costs nothing"


def test_a_party_that_is_too_far_away_is_not_helped(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion")

    async def move(db):
        (await db.get(Party, teams["bram"][1])).x += 5

    in_app_db(client, move)
    assert "too far" in expect(help_with(client, mike, aria, teams["bram"][1]), 422).json()["detail"]
    assert bram_vitals(client, bram) == {"HP": 20, "MP": 50}


@pytest.mark.parametrize("game", [NoComfort()], indirect=True)
def test_a_game_can_refuse_help_by_stance(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion")
    assert expect(help_with(client, mike, aria, teams["bram"][1]), 422).json()["detail"] == "Not today."


def test_a_hero_with_no_party_member_at_that_place_or_no_such_party_is_refused(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion")
    assert "no such member" in expect(help_with(client, mike, aria, teams["bram"][1], member=3), 422).json()["detail"]
    expect(help_with(client, mike, aria, 9999), 422)
    assert look(client, aria)["items"][0]["qty"] == 1


def test_an_item_that_does_nothing_for_the_target_is_not_wasted(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "sword")
    assert "can't be used" in expect(help_with(client, mike, aria, teams["bram"][1]), 422).json()["detail"]

    async def full(db):
        (await db.get(Hero, bram)).vitals = None

    in_app_db(client, full)
    give(client, aria, "potion")
    positions = {entry["item"]: entry["position"] for entry in look(client, aria)["items"]}
    assert "do nothing" in expect(help_with(client, mike, aria, teams["bram"][1], position=positions["potion"]), 422).json()["detail"]


# --- who may call it --------------------------------------------------------------------------------------------

def test_the_helper_must_be_the_callers_and_the_call_needs_the_csrf_token_and_a_strict_body(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion")
    party = teams["bram"][1]
    expect(help_with(client, mike, bram, party), 404)  # Bram is Zed's
    expect(client.post(f"/api/heroes/{aria}/help/use-item", json={"position": 0, "party_id": party, "member": 0}), 403)
    for body in ({"position": 0, "party_id": party}, {"position": 0, "party_id": 0, "member": 0}, {"position": -1, "party_id": party, "member": 0},
                 {"position": 0, "party_id": party, "member": 0, "target_hero_id": bram}, {"position": 0, "party_id": party, "member": "0"}):
        expect(client.post(f"/api/heroes/{aria}/help/use-item", json=body, headers=mike), 422)
    assert bram_vitals(client, bram) == {"HP": 20, "MP": 50}


def test_the_plain_use_item_call_still_refuses_another_players_hero(pair):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion")
    expect(client.post(f"/api/heroes/{aria}/use-item", json={"position": 0, "target_hero_id": bram}, headers=mike), 404)


def test_helping_is_limited_per_account(pair, monkeypatch):
    client, mike, aria, bram, teams = pair
    befriend(client, teams)
    give(client, aria, "potion", 3)
    monkeypatch.setattr(ratelimit, "HELP_BY_ACCOUNT", ratelimit.Limit("help", 1, 3600))
    expect(help_with(client, mike, aria, teams["bram"][1]), 200)
    expect(help_with(client, mike, aria, teams["bram"][1]), 429)


# --- the nearby list ---------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_the_nearby_list_for_help_shows_parties_like_talk():
    reach = Reach()
    assert await reach.lists_parties(None, HELP, None) and await reach.lists_parties(None, TALK, None)
    assert not await reach.lists_parties(None, "open", None)
