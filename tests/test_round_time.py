"""A player's round time setting: longer rounds for a price, as pure rules and over the calls. On every database."""

import pytest

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.fights import live, store, timing
from terraforma.fights.combatant import Combatant
from terraforma.fights.rules import Rules
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import PASSWORD
from .test_live_fights import RULES, a_team, attack

pytestmark = pytest.mark.anyio


# --- the rules (pure) ------------------------------------------------------------------------------------------------

def test_the_default_choices_are_one_one_and_a_half_and_two_for_nothing_five_and_ten_percent():
    rules = Rules()
    assert timing.offered(rules) == [1.0, 1.5, 2.0]
    assert [rules.time_bonus(each) for each in (1.0, 1.5, 2.0, 3.0)] == [0.0, 0.05, 0.10, 0.0]


def test_a_round_waits_the_multiple_of_the_games_round_time_never_less_than_one_times():
    rules = Rules()
    assert [rules.round_length(each) for each in (1.0, 1.5, 2.0, 0.5)] == [30, 45, 60, 30]


def test_a_fight_gets_the_longest_round_anyone_asked_for():
    assert timing.fight_multiplier([]) == 1.0
    assert timing.fight_multiplier([1.0, 2.0, 1.5]) == 2.0


def test_extra_hp_is_in_whole_percents_and_rounded_up():
    assert timing.boosted_hp(200, 0.10) == 220, "not a hair over, as floating point 200 * 1.1 would be"
    assert timing.boosted_hp(60, 0.05) == 63
    assert timing.boosted_hp(1, 0.05) == 2, "a rat is worth more to fight for longer"


def test_monsters_get_the_same_share_of_the_new_maximum_that_they_had_of_the_old():
    rules = Rules()
    half = Combatant("Ogre", {"HP": 60}, {"HP": 30})
    full = Combatant("Ogre", {"HP": 60}, {"HP": 60})
    timing.toughen([half, full], rules, 0.10)
    assert (half.base["HP"], half.current["HP"]) == (66, 33)
    assert (full.base["HP"], full.current["HP"]) == (66, 66)
    plain = Combatant("Ogre", {"HP": 60}, {"HP": 30})
    timing.toughen([plain], rules, 0.0)
    assert (plain.base["HP"], plain.current["HP"]) == (60, 30)


# --- the choice, and fights ------------------------------------------------------------------------------------------

async def test_a_player_who_never_chose_gets_normal_rounds_and_a_choice_is_kept_and_changed(db):
    _hero, mike, _team = await a_team(db)
    assert await timing.multiplier_for(db, mike.id) == 1.0
    await timing.choose(db, RULES, mike.id, 1.5)
    assert await timing.multiplier_for(db, mike.id) == 1.5
    await timing.choose(db, RULES, mike.id, 2.0)
    assert await timing.multiplier_for(db, mike.id) == 2.0


async def test_a_length_the_game_does_not_offer_is_refused(db):
    _hero, mike, _team = await a_team(db)
    with pytest.raises(timing.NotOffered):
        await timing.choose(db, RULES, mike.id, 3.0)
    assert await timing.multiplier_for(db, mike.id) == 1.0


async def test_longer_rounds_stretch_the_clock_in_every_round_and_toughen_the_monsters(db, later):
    _hero, mike, team = await a_team(db)
    await timing.choose(db, RULES, mike.id, 2.0)
    record = await live.start_team_fight(db, team, ["ogre"], RULES)
    assert record.time_multiplier == 2.0
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 60
    fight, _played = await store.load_state(db, record, RULES)
    assert fight.parties[1].groups[0].characters[0].base["HP"] == 66, "the ogre's 60 HP and 10% more"
    await attack(db, record, mike)
    later(10)
    await live.resolve_round(db, record, RULES)
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 60, "the next round waits as long"


async def test_normal_rounds_leave_the_monsters_and_the_clock_alone(db):
    _hero, _mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["ogre"], RULES)
    assert record.time_multiplier == 1.0
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 30
    fight, _played = await store.load_state(db, record, RULES)
    assert fight.parties[1].groups[0].characters[0].base["HP"] == 60


async def test_a_choice_counts_only_for_fights_started_after_it(db):
    _hero, mike, team = await a_team(db)
    first = await live.start_team_fight(db, team, ["rat"], RULES)
    await timing.choose(db, RULES, mike.id, 2.0)
    assert first.time_multiplier == 1.0


async def test_the_games_choices_and_prices_are_what_applies(db):
    class Generous(Rules):
        time_multipliers = ((1.0, 0.0), (3.0, 0.0))

    _hero, mike, team = await a_team(db)
    rules = Generous()
    with pytest.raises(timing.NotOffered):
        await timing.choose(db, rules, mike.id, 1.5)
    await timing.choose(db, rules, mike.id, 3.0)
    record = await live.start_team_fight(db, team, ["ogre"], rules)
    assert live.aware(record.round_deadline).timestamp() == wallclock.now().timestamp() + 90
    fight, _played = await store.load_state(db, record, rules)
    assert fight.parties[1].groups[0].characters[0].base["HP"] == 60, "this game charges nothing"


# --- the calls -------------------------------------------------------------------------------------------------------

def signed_in(client):
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    token = expect(client.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def test_a_player_looks_at_the_choices_and_chooses_over_the_calls(app_client):
    headers = signed_in(app_client)
    seen = expect(app_client.get("/api/settings/round-time"), 200).json()
    assert seen == {
        "multiplier": 1.0, "round_seconds": 30,
        "options": [{"multiplier": 1.0, "monster_hp_bonus": 0.0}, {"multiplier": 1.5, "monster_hp_bonus": 0.05}, {"multiplier": 2.0, "monster_hp_bonus": 0.1}],
    }
    chosen = expect(app_client.post("/api/settings/round-time", json={"multiplier": 1.5}, headers=headers), 200).json()
    assert chosen["multiplier"] == 1.5 and chosen["round_seconds"] == 45
    assert expect(app_client.get("/api/settings/round-time"), 200).json()["multiplier"] == 1.5


def test_the_calls_need_a_login_and_a_choice_needs_the_csrf_token(app_client):
    assert app_client.get("/api/settings/round-time").status_code == 401
    headers = signed_in(app_client)
    expect(app_client.post("/api/settings/round-time", json={"multiplier": 2.0}), 403)
    assert app_client.get("/api/settings/round-time").json()["multiplier"] == 1.0
    assert headers


def test_the_call_takes_only_a_number_the_game_offers(app_client):
    headers = signed_in(app_client)
    expect(app_client.post("/api/settings/round-time", json={"multiplier": 3.0}, headers=headers), 422)
    expect(app_client.post("/api/settings/round-time", json={"multiplier": "2"}, headers=headers), 422)
    expect(app_client.post("/api/settings/round-time", json={"multiplier": 2.0, "extra": 1}, headers=headers), 422)
    assert app_client.get("/api/settings/round-time").json()["multiplier"] == 1.0
