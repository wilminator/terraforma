"""Fights between parties: PvP is off by default, a game opens places, and the range window picks who may be fought."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Hero
from terraforma.parties import service as parties
from terraforma.pvp.service import PvpError, start_pvp_fight
from terraforma.testing import in_app_db
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.models import Account
from terraforma.game import Game
from terraforma.pvp import service
from terraforma.pvp.hooks import PvpZones

from .helpers import expect

pytestmark = pytest.mark.anyio


class Strength(Rules):
    """Rules where a fighter is just its strength."""

    def pxp(self, fighter) -> int:
        return fighter


class Wilds(PvpZones):
    """Everywhere but map 1 (a town) permits PvP."""

    async def allows_pvp(self, session, map_id, x, y):
        return map_id != 1


def test_refused_where_pvp_is_off():
    assert Rules().may_start_pvp(100, 500, False) is not None


def test_a_stronger_or_equal_party_is_always_allowed():
    rules = Rules()
    assert rules.may_start_pvp(100, 100, True) is None
    assert rules.may_start_pvp(100, 10_000, True) is None


def test_the_window_is_85_percent_by_default():
    rules = Rules()
    assert rules.may_start_pvp(100, 85, True) is None
    assert rules.may_start_pvp(100, 84, True) is not None


def test_a_game_can_change_the_window():
    class Wide(Rules):
        pvp_window = 0.5

    assert Wide().may_start_pvp(100, 50, True) is None
    assert Wide().may_start_pvp(100, 49, True) is not None


def test_party_pxp_sums_its_fighters():
    assert service.party_pxp(Strength(), [10, 20, 30]) == 60


async def test_default_zones_allow_nowhere():
    assert await PvpZones().allows_pvp(None, 1, 0, 0) is False
    assert isinstance(Game(name="Test").pvp, PvpZones)


async def test_refusal_uses_the_place_and_the_summed_parties():
    rules = Strength()
    assert await service.refusal(None, Wilds(), rules, [50, 50], [90], 2, 0, 0) is None
    assert await service.refusal(None, Wilds(), rules, [50, 50], [84], 2, 0, 0) is not None
    assert await service.refusal(None, Wilds(), rules, [50, 50], [900], 1, 0, 0) is not None


# --- picking a fight -------------------------------------------------------------------------------------------------

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20, "MP": 2, "Speed": 5, "Strength": 5}}]}


class Levelled(Rules):
    """Rules where a fighter is worth 100 for each level."""

    def pxp(self, fighter) -> int:
        return fighter.level * 100


class Everywhere(PvpZones):
    async def allows_pvp(self, session, map_id, x, y):
        return True


async def a_party(db, owner, name, hero_name, level=1):
    team = await heroes.create_team(db, owner, name)
    hero = await heroes.create_hero(db, owner, hero_name, "fighter")
    hero.level = level
    await heroes.add_to_team(db, owner, team.id, hero.id)
    return team, await parties.create_party(db, team.id, 20)


async def two_parties(db, mine=1, theirs=1):
    """Mike's Vanguard (Aria) and Zed's Scouts (Cleo), each in a party of its own, at the same spot."""
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    team, party = await a_party(db, mike, "Vanguard", "Aria", mine)
    _other, foes = await a_party(db, zed, "Scouts", "Cleo", theirs)
    return mike, zed, team, party, foes


async def test_a_party_picks_a_fight_with_another_where_pvp_is_allowed(db):
    mike, zed, team, _party, foes = await two_parties(db)
    record = await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)
    rows = (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == record.id).order_by(FightParticipant.party))).all()
    assert [(row.party, row.name) for row in rows] == [(0, "Aria"), (1, "Cleo")] and record.finished is False
    assert record.round_deadline is not None


async def test_the_fight_is_refused_where_pvp_is_off(db):
    mike, _zed, team, _party, foes = await two_parties(db)
    with pytest.raises(PvpError, match="not allowed here"):
        await start_pvp_fight(db, PvpZones(), Levelled(), mike, team.id, foes.id)
    assert await db.scalar(select(FightRecord.id)) is None


async def test_a_party_too_weak_is_refused_and_a_stronger_one_is_not(db):
    mike, _zed, team, _party, foes = await two_parties(db, mine=10, theirs=8)
    with pytest.raises(PvpError, match="too weak"):
        await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)
    for hero in (await db.scalars(select(Hero))).all():  # swap the levels: now the target is stronger
        hero.level = 18 - hero.level
    assert await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id) is not None


async def test_a_team_must_be_the_callers_and_in_a_party(db):
    mike, zed, team, _party, foes = await two_parties(db)
    with pytest.raises(heroes.NotFound):
        await start_pvp_fight(db, Everywhere(), Levelled(), zed, team.id, foes.id)
    alone = await heroes.create_team(db, mike, "Alone")
    with pytest.raises(PvpError, match="in a party"):
        await start_pvp_fight(db, Everywhere(), Levelled(), mike, alone.id, foes.id)


async def test_a_party_cannot_fight_itself_a_missing_party_or_one_elsewhere(db):
    mike, _zed, team, party, foes = await two_parties(db)
    for target, reason in ((party.id, "itself"), (foes.id + 99, "no such party")):
        with pytest.raises(PvpError, match=reason):
            await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, target)
    foes.x += 1
    await db.flush()
    with pytest.raises(PvpError, match="not here"):
        await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)


async def test_a_party_in_a_town_or_a_fight_cannot_pick_one(db):
    mike, _zed, team, _party, foes = await two_parties(db)
    await towns_service.enter_town(db, Towns(), foes.id)
    with pytest.raises(PvpError, match="in a town"):
        await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)


async def test_a_hero_already_in_a_fight_blocks_a_second_fight(db):
    mike, _zed, team, _party, foes = await two_parties(db)
    await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)
    with pytest.raises(PvpError, match="already in a fight"):
        await start_pvp_fight(db, Everywhere(), Levelled(), mike, team.id, foes.id)


# --- the call ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, pvp=Everywhere())


def test_the_call_picks_a_fight_and_says_why_not(app_client):
    in_app_db(app_client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    token = app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    headers = {"X-CSRF-Token": token}
    ids = {}

    async def setup(db):
        zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
        mike = await db.scalar(select(Account).where(Account.username == "Mike"))
        team, _party = await a_party(db, mike, "Vanguard", "Aria")
        _other, foes = await a_party(db, zed, "Scouts", "Cleo")
        ids.update(team=team.id, foes=foes.id)

    in_app_db(app_client, setup)
    url = "/api/pvp/fights"
    expect(app_client.post(url, json={"team_id": ids["team"], "party_id": ids["foes"]}), 403)  # no CSRF token
    expect(app_client.post(url, json={"team_id": ids["team"]}, headers=headers), 422)
    expect(app_client.post(url, json={"team_id": ids["team"], "party_id": ids["foes"], "extra": 1}, headers=headers), 422)
    expect(app_client.post(url, json={"team_id": ids["team"] + 99, "party_id": ids["foes"]}, headers=headers), 404)
    expect(app_client.post(url, json={"team_id": ids["team"], "party_id": ids["foes"] + 99}, headers=headers), 409)
    started = expect(app_client.post(url, json={"team_id": ids["team"], "party_id": ids["foes"]}, headers=headers), 201).json()
    assert started["fight"] and started["guid"]
    assert "already in a fight" in expect(app_client.post(url, json={"team_id": ids["team"], "party_id": ids["foes"]}, headers=headers), 409).json()["detail"]
