"""The coliseum: one team asks another to a bout, the other answers, and an accepted bout is a public fight. On every database."""

import pytest
from sqlalchemy import select

from datetime import timedelta

from terraforma import testing, wallclock
from terraforma.coliseum import service as coliseum
from terraforma.coliseum.models import EXPIRED, FOUGHT, Bout
from terraforma.fightpages.models import PublicFight
from terraforma.fights import live
from terraforma.fights.models import FightRecord
from terraforma.fights.rules import Rules
from terraforma.fighttags import service as tags
from terraforma.housekeeping import old_unkept_fights
from terraforma.settings import Settings
from terraforma.game import Game
from terraforma.heroes.models import TeamMember
from terraforma.testing import in_app_db

from .helpers import expect
from .test_inventory import sign_in
from .test_fight_pages import a_second_team, real_fight
from .test_live_fights import RULES, a_team
from .test_party_help import login

pytestmark = pytest.mark.social


@pytest.fixture
def game(tmp_path):
    import json

    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps([{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10}, "abilities": []}]))
    return Game(name="Test Game", seed_dir=seed_dir)


@pytest.fixture
def duel(app_client):
    """Mike's Aria and Zed's Bram, each alone on a team; returns (client, mike's headers, zed's headers, aria's team, bram's team)."""
    mike = sign_in(app_client, "Mike")
    aria = testing.make_hero(app_client, mike, "Aria")["id"]
    zed = sign_in(app_client, "Zed")
    bram = testing.make_hero(app_client, zed, "Bram")["id"]
    mike, zed = "Mike", "Zed"  # (one client holds one session: a call names who makes it, and logs them in first)

    async def teams(db):
        return [await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero)) for hero in (aria, bram)]

    mine, theirs = in_app_db(app_client, teams)
    return app_client, mike, zed, mine, theirs


def ask(client, who, team, other):
    return client.post(f"/api/teams/{team}/coliseum/challenge", json={"team_id": other}, headers=login(client, who))


def answer(client, who, team, bout, what):
    return client.post(f"/api/teams/{team}/coliseum/{bout}/{what}", headers=login(client, who))


def test_a_challenge_accepted_is_a_fight_that_will_be_public(duel):
    client, mike, zed, mine, theirs = duel
    bout = expect(ask(client, mike, mine, theirs), 200).json()["bout"]
    assert (bout["challenger"], bout["challenged"], bout["state"], bout["fight_id"]) == (mine, theirs, "offered", None)
    fought = expect(answer(client, zed, theirs, bout["id"], "accept"), 200).json()["bout"]
    assert fought["state"] == FOUGHT and fought["fight_id"]

    async def look(db):
        record = await db.get(FightRecord, fought["fight_id"])
        return record.finished, record.monster_play, record.round_deadline is not None

    assert in_app_db(client, look) == (False, False, True), "running, without a monster seat, and waiting on its first round"
    login(client, "Mike")
    listed = expect(client.get(f"/api/teams/{mine}/coliseum"), 200).json()["bouts"]
    assert [each["id"] for each in listed] == [bout["id"]]


def test_refusals_cost_nothing(duel):
    client, mike, zed, mine, theirs = duel
    assert "your own team" in expect(ask(client, mike, mine, mine), 422).json()["detail"]
    assert "no such team" in expect(ask(client, mike, mine, 9999), 404).json()["detail"]
    bout = expect(ask(client, mike, mine, theirs), 200).json()["bout"]
    assert "already been asked" in expect(ask(client, mike, mine, theirs), 422).json()["detail"]
    assert "no such bout" in expect(answer(client, mike, mine, bout["id"], "accept"), 404).json()["detail"], "the asker cannot accept their own offer"
    assert "no such bout" in expect(answer(client, zed, theirs, bout["id"], "withdraw"), 404).json()["detail"]
    assert in_app_db(client, lambda db: db.scalar(select(FightRecord.id))) is None


def test_decline_and_withdraw_close_the_offer(duel):
    client, mike, zed, mine, theirs = duel
    first = expect(ask(client, mike, mine, theirs), 200).json()["bout"]["id"]
    assert expect(answer(client, zed, theirs, first, "decline"), 200).json()["bout"]["state"] == "declined"
    assert "declined" in expect(answer(client, zed, theirs, first, "accept"), 422).json()["detail"]
    second = expect(ask(client, mike, mine, theirs), 200).json()["bout"]["id"]
    assert expect(answer(client, mike, mine, second, "withdraw"), 200).json()["bout"]["state"] == "withdrawn"


def test_an_offer_that_runs_out_cannot_be_accepted(duel):
    client, mike, zed, mine, theirs = duel
    late = expect(ask(client, mike, mine, theirs), 200).json()["bout"]["id"]

    async def age(db):
        (await db.get(Bout, late)).expires_at = wallclock.timestamp() - 1

    in_app_db(client, age)
    assert "expired" in expect(answer(client, zed, theirs, late, "accept"), 422).json()["detail"]
    assert in_app_db(client, lambda db: db.scalar(select(FightRecord.id))) is None, "no fight was made"


def test_housekeeping_closes_late_offers(duel):
    client, mike, zed, mine, theirs = duel
    late = expect(ask(client, mike, mine, theirs), 200).json()["bout"]["id"]

    async def work(db):
        (await db.get(Bout, late)).expires_at = wallclock.timestamp() - 1
        closed = await coliseum.expire(db)
        return closed, (await db.get(Bout, late)).state

    assert in_app_db(client, work) == (1, EXPIRED)


def test_two_teams_of_one_account_cannot_be_matched(app_client):
    mike = sign_in(app_client, "Mike")
    first = testing.make_hero(app_client, mike, "Aria")
    second = testing.make_hero(app_client, mike, "Bram")

    async def teams(db):
        return [await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero["id"])) for hero in (first, second)]

    one, two = in_app_db(app_client, teams)
    assert "your own team" in expect(ask(app_client, "Mike", one, two), 422).json()["detail"]


@pytest.mark.anyio
async def test_a_bouts_fight_is_public_when_it_is_recorded_and_kept_for_good(db):
    hero, mike, team = await a_team(db)
    _zhero, zed, zteam = await a_second_team(db)
    rules = Rules()
    bout = await coliseum.challenge(db, rules, mike, team.id, zteam.id)
    bout = await coliseum.accept(db, rules, zed, bout.id, zteam.id)
    record = await db.get(FightRecord, bout.fight_id)
    fight, _played = await live.store.load_state(db, record, RULES)
    fight.over = True
    await tags.record_fight(db, record, fight, rules, [])
    record.finished, record.updated_at = True, wallclock.now() - timedelta(days=400)
    await db.flush()
    assert [row.fight_id for row in (await db.scalars(select(PublicFight))).all()] == [bout.fight_id]
    assert await old_unkept_fights(db, wallclock.timestamp(), Settings(session_secret="x" * 32), rules) == 0, "a public fight is kept for good"
    other = await real_fight(db, team)
    fight, _played = await live.store.load_state(db, other, RULES)
    fight.over = True
    await tags.record_fight(db, other, fight, rules, [])
    assert [row.fight_id for row in (await db.scalars(select(PublicFight))).all()] == [bout.fight_id], "a fight with no bout is not made public"
