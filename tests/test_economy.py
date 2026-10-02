"""The economy: where gold lives (the team, DragonStar's way, or the hero), how it moves, and a fight's gold paid once."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.economy import Economy, HeroGold, NotEnoughGold, TeamGold, add_gold, credit_fight_gold
from terraforma.fights.models import FightRecord
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.heroes.models import Hero, Team
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start
pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}


async def account(db):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the account)
    return await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)


async def team_with(db, owner, names):
    team = await service.create_team(db, owner, "Vanguard")
    heroes = []
    for name in names:
        hero = await service.create_hero(db, owner, name, "fighter")
        await service.add_to_team(db, owner, team.id, hero.id)
        heroes.append(hero)
    return team, heroes


# --- the team's way (the default) ---------------------------------------------------------------------------------------

async def test_a_hero_on_a_team_shares_the_teams_gold(db):
    mike = await account(db)
    team, (aria, bram) = await team_with(db, mike, ["Aria", "Bram"])
    economy = TeamGold()
    await economy.credit(db, aria, 30)
    assert await economy.balance(db, bram) == 30 and (await db.get(Team, team.id)).gold == 30 and (await db.get(Hero, aria.id)).gold == 0
    await economy.debit(db, bram, 12)
    assert await economy.balance(db, aria) == 18


async def test_a_hero_on_no_team_has_gold_of_their_own(db):
    mike = await account(db)
    loner = await service.create_hero(db, mike, "Loner", "fighter")
    await TeamGold().credit(db, loner, 5)
    assert await TeamGold().balance(db, loner) == 5 and (await db.get(Hero, loner.id)).gold == 5


async def test_spending_more_than_there_is_takes_nothing(db):
    mike = await account(db)
    _team, (aria,) = await team_with(db, mike, ["Aria"])
    economy = TeamGold()
    await economy.credit(db, aria, 10)
    with pytest.raises(NotEnoughGold):
        await economy.debit(db, aria, 11)
    assert await economy.balance(db, aria) == 10
    await economy.debit(db, aria, 10)
    assert await economy.balance(db, aria) == 0


async def test_gold_moves_by_whole_positive_amounts_only(db):
    mike = await account(db)
    aria = await service.create_hero(db, mike, "Aria", "fighter")
    for call in (TeamGold().credit(db, aria, -1), TeamGold().debit(db, aria, -1)):
        with pytest.raises(ValueError, match="positive"):
            await call


async def test_what_a_team_earns_goes_to_the_team(db):
    mike = await account(db)
    team, (aria,) = await team_with(db, mike, ["Aria"])
    await TeamGold().credit_team(db, team.id, 40)
    await TeamGold().credit_team(db, 999, 40)  # a team deleted since the fight began: nowhere to put it, and no harm
    assert await TeamGold().balance(db, aria) == 40


# --- the hero's way --------------------------------------------------------------------------------------------------------

async def test_with_hero_gold_every_hero_keeps_their_own(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, ["Aria", "Bram"])
    economy = HeroGold()
    await economy.credit(db, aria, 9)
    assert (await economy.balance(db, aria), await economy.balance(db, bram)) == (9, 0)
    await economy.debit(db, aria, 4)
    assert await economy.balance(db, aria) == 5


async def test_what_a_team_earns_is_split_between_its_heroes_and_the_odd_coins_go_first(db):
    mike = await account(db)
    team, (aria, bram, cato) = await team_with(db, mike, ["Aria", "Bram", "Cato"])
    await HeroGold().credit_team(db, team.id, 10)
    assert [await HeroGold().balance(db, hero) for hero in (aria, bram, cato)] == [4, 3, 3]
    await HeroGold().credit_team(db, team.id, 2)
    assert [await HeroGold().balance(db, hero) for hero in (aria, bram, cato)] == [5, 4, 3]
    empty = await service.create_team(db, mike, "Empty")
    await HeroGold().credit_team(db, empty.id, 10)  # nobody to pay: nothing happens


async def test_a_game_can_decide_for_itself_where_gold_lives(db):
    class Guild(Economy):
        """All gold on the account's first hero."""

        async def purse(self, session, hero):
            return await session.scalar(select(Hero).where(Hero.account_id == hero.account_id).order_by(Hero.id).limit(1))

        async def credit_team(self, session, team_id, amount):
            raise AssertionError("not used here")

    mike = await account(db)
    aria, bram = await service.create_hero(db, mike, "Aria", "fighter"), await service.create_hero(db, mike, "Bram", "fighter")
    await Guild().credit(db, bram, 7)
    assert await Guild().balance(db, aria) == 7


# --- a fight's gold, paid once -----------------------------------------------------------------------------------------------

async def a_fight(db):
    hub = await ensure_start(db)
    record = FightRecord(guid="g" * 32, initial_state={}, map_id=hub.id, x=0, y=0)
    db.add(record)
    await db.flush()
    return record


async def test_a_fights_gold_is_paid_once_however_often_it_is_asked_for(db):
    mike = await account(db)
    team, (aria,) = await team_with(db, mike, ["Aria"])
    record = await a_fight(db)
    assert record.gold_paid is False
    assert await credit_fight_gold(db, TeamGold(), record, [(team.id, 25)]) is True
    assert await credit_fight_gold(db, TeamGold(), record, [(team.id, 25)]) is False
    assert await TeamGold().balance(db, aria) == 25 and record.gold_paid is True


async def test_a_fight_pays_each_team_and_skips_nothing_payments(db):
    mike = await account(db)
    one, (aria,) = await team_with(db, mike, ["Aria"])
    two = await service.create_team(db, mike, "Second")
    record = await a_fight(db)
    await credit_fight_gold(db, TeamGold(), record, [(one.id, 10), (two.id, 0), (one.id, 5)])
    assert await TeamGold().balance(db, aria) == 15 and (await db.get(Team, two.id)).gold == 0


async def test_add_gold_is_one_statement_so_the_balance_reads_back(db):
    mike = await account(db)
    team, _heroes = await team_with(db, mike, ["Aria"])
    await add_gold(db, team, 3)
    await add_gold(db, team, 4)
    assert team.gold == 7


# --- the call -------------------------------------------------------------------------------------------------------------------

@pytest.fixture(params=["team", "hero"])
def game(request, tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    return Game(name="Test", seed_dir=seed_dir, economy=TeamGold() if request.param == "team" else HeroGold())


@pytest.mark.anyio(False)
def test_the_inventory_shows_the_gold_the_games_economy_says(app_client, game):
    client = app_client
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    team = client.post("/api/teams", json={"name": "Vanguard"}, headers=headers).json()["id"]
    hero = client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=headers).json()["id"]
    assert client.post(f"/api/teams/{team}/add-hero", json={"hero_id": hero}, headers=headers).status_code == 200

    async def give(db):
        await add_gold(db, await db.get(Team, team), 7)
        await add_gold(db, await db.get(Hero, hero), 2)

    in_app_db(client, give)
    shown = client.get(f"/api/heroes/{hero}/inventory").json()["gold"]
    assert shown == (7 if isinstance(game.economy, TeamGold) else 2)
