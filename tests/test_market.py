"""The market: shops standing on a map, what they stock and charge, buying and selling, and the calls, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Item, Job
from terraforma.economy import HeroGold, TeamGold
from terraforma.fights.build import hero_fighter
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import inventory, service
from terraforma.heroes.models import Hero, HeroEquipment, Team
from terraforma.models import Account
from terraforma.parties import service as parties
from terraforma.market import service as market
from terraforma.market.hooks import Market
from terraforma.market.models import Shop
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import a_team_fights_a_rat

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}],
    "items": [
        {"key": "herb", "name": "Herb", "price": 10},
        {"key": "potion", "name": "Potion", "price": 25, "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "sword", "name": "Sword", "price": 100, "equip_slots": ["hand"], "stat_bonus": {"Strength": 3}},
        {"key": "crown", "name": "Crown", "price": 1_000_000},
        {"key": "keepsake", "name": "Keepsake", "price": 0},
    ],
}


class Flat(Market):
    """Every shop is at economy level 1000, whoever asks."""

    async def level(self, session, rules, shop, hero, party):
        return 1000


class Herbalist(Flat):
    """A quest: the area needs herbs, so the shop pays double for them."""

    def sell_percent(self, shop, item):
        return 200 if item.key == "herb" else 75


async def a_hero(db, name="Aria", team_gold=500):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    owner = await create_account(db, f"{name}Owner", PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
    team = await service.create_team(db, owner, f"{name}Team")
    hero = await service.create_hero(db, owner, name, "fighter")
    await service.add_to_team(db, owner, team.id, hero.id)
    team.gold = team_gold
    await db.flush()
    return hero, team


async def shop_at(db, hero, key="general"):
    return await market.place_shop(db, key, "General Store", hero.map_id, hero.x, hero.y)


async def holding(db, hero, key):
    return sum(stack.qty for stack, item in await inventory.stacks(db, hero) if item.key == key)


async def gold(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


TeamGoldEconomy = TeamGold()


async def buy(db, hero, shop, key, qty=1, market_rules=None):
    return await market.buy(db, market_rules or Flat(), Rules(), TeamGoldEconomy, hero, shop.id, key, qty)


# --- where shops stand ----------------------------------------------------------------------------------------------

@anyio
async def test_placing_a_shop_twice_moves_and_renames_it_instead_of_adding_another(db):
    hero, _team = await a_hero(db)
    first = await shop_at(db, hero)
    again = await market.place_shop(db, "general", "Better Store", hero.map_id, 3, 4)
    assert again.id == first.id and (again.name, again.x, again.y) == ("Better Store", 3, 4)
    assert await db.scalar(select(func.count()).select_from(Shop)) == 1


@anyio
async def test_a_hero_sees_and_shops_only_at_the_shops_on_their_own_tile(db):
    hero, _team = await a_hero(db)
    here = await shop_at(db, hero, "here")
    far = await market.place_shop(db, "far", "Far Store", hero.map_id, hero.x + 5, hero.y)
    assert [shop.id for shop in await market.shops_here(db, hero)] == [here.id]
    with pytest.raises(market.NoSuchShop):
        await market.view(db, Flat(), Rules(), TeamGoldEconomy, hero, far.id)
    with pytest.raises(market.NoSuchShop):
        await market.view(db, Flat(), Rules(), TeamGoldEconomy, hero, 9999)
    hero.x += 5  # walks over
    assert [shop.id for shop in await market.shops_here(db, hero)] == [far.id]


# --- stock and level -------------------------------------------------------------------------------------------------

@anyio
async def test_by_default_the_level_is_the_partys_pxp_and_the_shop_sells_what_is_priced_at_or_under_it(db):
    hero, _team = await a_hero(db)
    shop = await shop_at(db, hero)
    expected = int(Rules().pxp(await hero_fighter(db, hero)))
    seen = await market.view(db, Market(), Rules(), TeamGoldEconomy, hero, shop.id)
    assert seen["level"] == expected
    items = (await db.scalars(select(Item))).all()
    wanted = sorted((item for item in items if 0 < item.price <= expected), key=lambda item: (item.price, item.name))
    assert [ware["item"] for ware in seen["wares"]] == [item.key for item in wanted]
    assert "keepsake" not in [ware["item"] for ware in seen["wares"]], "an item with no price is never for sale"


@anyio
async def test_the_whole_team_shops_together_so_the_level_is_all_its_heroes(db):
    aria, team = await a_hero(db, "Aria")
    owner = await db.get(Account, aria.account_id)
    bram = await service.create_hero(db, owner, "Bram", "fighter")
    await service.add_to_team(db, owner, team.id, bram.id)
    shop = await shop_at(db, aria)
    one = int(Rules().pxp(await hero_fighter(db, aria)))
    both = int(Rules().pxp(await hero_fighter(db, aria)) + Rules().pxp(await hero_fighter(db, bram)))
    assert both > one > 0
    assert [hero.id for hero in await market.shoppers(db, aria)] == [aria.id, bram.id]
    assert await market.level_of(db, Market(), Rules(), shop, aria) == both


@anyio
async def test_a_party_shops_together_across_its_teams(db):
    aria, team = await a_hero(db, "Aria")
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    other = await service.create_team(db, zed, "Rearguard")
    cole = await service.create_hero(db, zed, "Cole", "fighter")
    await service.add_to_team(db, zed, other.id, cole.id)
    party = await parties.create_party(db, team.id, 4)
    await parties.join_party(db, party.id, other.id, 4)
    assert [hero.id for hero in await market.shoppers(db, cole)] == [aria.id, cole.id]


@anyio
async def test_a_hero_in_no_team_shops_alone(db):
    aria, _team = await a_hero(db)
    loner = await service.create_hero(db, await db.get(Account, aria.account_id), "Loner", "fighter")
    assert [hero.id for hero in await market.shoppers(db, loner)] == [loner.id]


@anyio
async def test_a_game_decides_the_stock_the_price_and_the_level(db):
    hero, team = await a_hero(db)
    shop = await shop_at(db, hero)

    class Regional(Flat):
        async def stock(self, session, shop, level):
            return [item for item in await super().stock(session, shop, level) if item.key == "sword"]

        def price(self, shop, item):
            return item.price * 2

    seen = await market.view(db, Regional(), Rules(), TeamGoldEconomy, hero, shop.id)
    assert (seen["level"], [(ware["item"], ware["price"]) for ware in seen["wares"]]) == (1000, [("sword", 200)])
    result = await buy(db, hero, shop, "sword", 1, Regional())
    assert result["cost"] == 200 and await gold(db, team) == 300
    with pytest.raises(market.ShopError, match="isn't for sale"):
        await buy(db, hero, shop, "herb", 1, Regional())


# --- buying ----------------------------------------------------------------------------------------------------------

@anyio
async def test_buying_charges_the_teams_purse_and_fills_the_pack(db):
    hero, team = await a_hero(db)
    shop = await shop_at(db, hero)
    result = await buy(db, hero, shop, "potion", 3)
    assert result == {"bought": 3, "not_fitting": 0, "cost": 75, "gold": 425}
    assert await holding(db, hero, "potion") == 3 and await gold(db, team) == 425


@anyio
async def test_a_purchase_the_purse_cannot_cover_changes_nothing(db):
    hero, team = await a_hero(db, team_gold=60)
    shop = await shop_at(db, hero)
    with pytest.raises(market.ShopError, match="costs more"):
        await buy(db, hero, shop, "potion", 3)
    assert await holding(db, hero, "potion") == 0 and await gold(db, team) == 60, "no items, no charge"


@anyio
async def test_only_what_the_game_stocks_at_the_level_can_be_bought(db):
    hero, team = await a_hero(db, team_gold=10**7)
    shop = await shop_at(db, hero)
    for key in ("crown", "keepsake", "nonsense"):
        with pytest.raises(market.ShopError, match="isn't for sale"):
            await buy(db, hero, shop, key)
    item = await db.scalar(select(Item).where(Item.key == "herb"))
    item.active = False
    await db.flush()
    with pytest.raises(market.ShopError, match="isn't for sale"):
        await buy(db, hero, shop, "herb")
    assert await gold(db, team) == 10**7


@anyio
async def test_equipment_is_bought_one_at_a_time_and_a_bad_quantity_is_refused(db):
    hero, _team = await a_hero(db)
    shop = await shop_at(db, hero)
    for qty in (0, -1, market.MAX_QTY + 1, 2):
        with pytest.raises(market.ShopError, match="how many"):
            await buy(db, hero, shop, "sword", qty)
    assert (await buy(db, hero, shop, "sword", 1))["bought"] == 1


@anyio
async def test_a_full_pack_buys_nothing_and_a_nearly_full_one_is_charged_only_for_what_fits(db):
    hero, team = await a_hero(db, team_gold=10_000)
    shop = await shop_at(db, hero)
    await inventory.add_item(db, hero, "herb", inventory.MAX_ITEM_QTY - 1)  # one stack, one short of full
    for _ in range(inventory.MAX_ITEMS - 1):
        await inventory.add_item(db, hero, "sword", 1)  # the other eleven slots
    result = await buy(db, hero, shop, "herb", 3)
    assert result == {"bought": 1, "not_fitting": 2, "cost": 10, "gold": 9990}
    assert await gold(db, team) == 9990
    with pytest.raises(market.ShopError, match="can't carry"):
        await buy(db, hero, shop, "herb", 1)
    assert await gold(db, team) == 9990, "a refused purchase costs nothing"


@anyio
async def test_a_hero_in_a_running_fight_cannot_shop(db):
    hero, _record = await a_team_fights_a_rat(db)
    shop = await shop_at(db, hero)
    with pytest.raises(market.ShopError, match="in a fight"):
        await market.buy(db, Flat(), Rules(), TeamGoldEconomy, hero, shop.id, "anything", 1)
    with pytest.raises(market.ShopError, match="in a fight"):
        await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 0, 1)


@anyio
async def test_gold_goes_where_the_games_economy_keeps_it(db):
    hero, team = await a_hero(db)
    hero.gold = 40
    await db.flush()

    class Own(HeroGold):
        pass

    shop = await shop_at(db, hero)
    result = await market.buy(db, Flat(), Rules(), Own(), hero, shop.id, "potion", 1)
    assert result["gold"] == 15 and hero.gold == 15 and await gold(db, team) == 500, "the hero's own gold, not the team's"


# --- selling ---------------------------------------------------------------------------------------------------------

@anyio
async def test_selling_pays_seventy_five_percent_rounded_half_up_into_the_teams_purse(db):
    hero, team = await a_hero(db, team_gold=0)
    shop = await shop_at(db, hero)
    await inventory.add_item(db, hero, "sword", 1)   # position 0: 100 -> 75
    await inventory.add_item(db, hero, "potion", 4)  # position 1: 25 -> 18.75 -> 19 each
    assert (await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 0, 1))["paid"] == 75
    result = await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 0, 3)  # the potions slid to position 0
    assert result == {"sold": 3, "paid": 57, "gold": 132}
    assert await holding(db, hero, "potion") == 1 and await gold(db, team) == 132


@anyio
async def test_a_game_overrides_the_sell_back_for_one_item(db):
    hero, team = await a_hero(db, team_gold=0)
    shop = await shop_at(db, hero)
    await inventory.add_item(db, hero, "herb", 5)
    seen = await market.view(db, Herbalist(), Rules(), TeamGoldEconomy, hero, shop.id)
    assert [(each["item"], each["each"]) for each in seen["sellable"]] == [("herb", 20)]
    result = await market.sell(db, Herbalist(), TeamGoldEconomy, hero, shop.id, 0, 5)
    assert result["paid"] == 100 and await gold(db, team) == 100


@anyio
async def test_selling_refuses_a_missing_stack_a_bad_quantity_and_what_would_pay_nothing(db):
    hero, team = await a_hero(db, team_gold=0)
    shop = await shop_at(db, hero)
    await inventory.add_item(db, hero, "potion", 2)
    await inventory.add_item(db, hero, "keepsake", 1)
    with pytest.raises(market.ShopError, match="doesn't have"):
        await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 7, 1)
    for qty in (0, 3):
        with pytest.raises(market.ShopError, match="how many"):
            await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 0, qty)
    with pytest.raises(market.ShopError, match="won't pay anything"):
        await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 1, 1)
    assert await holding(db, hero, "keepsake") == 1 and await gold(db, team) == 0


@anyio
async def test_selling_what_is_worn_takes_it_off(db):
    hero, _team = await a_hero(db)
    shop = await shop_at(db, hero)
    await inventory.add_item(db, hero, "sword", 1)
    await inventory.equip(db, hero, 0)
    assert await db.scalar(select(func.count()).select_from(HeroEquipment)) == 1
    await market.sell(db, Flat(), TeamGoldEconomy, hero, shop.id, 0, 1)
    assert await db.scalar(select(func.count()).select_from(HeroEquipment)) == 0


# --- the calls -------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, market=Herbalist())


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


@pytest.fixture
def town(app_client):
    """Mike's Aria, on a team with 500 gold, standing where a shop is; Zed's Zara stands there too."""
    mike = sign_in(app_client, "Mike")
    aria = expect(app_client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=mike), 201).json()["id"]
    team = expect(app_client.post("/api/teams", json={"name": "Vanguard"}, headers=mike), 201).json()["id"]
    expect(app_client.post(f"/api/teams/{team}/add-hero", json={"hero_id": aria}, headers=mike), 200)

    async def set_up(db):
        db_team = await db.get(Team, team)
        db_team.gold = 500
        return (await market.place_shop(db, "general", "General Store", (await db.get(Hero, aria)).map_id, 0, 0)).id

    shop = in_app_db(app_client, set_up)
    zed = sign_in(app_client, "Zed")
    zara = expect(app_client.post("/api/heroes", json={"name": "Zara", "job": "fighter"}, headers=zed), 201).json()["id"]
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    return app_client, mike, aria, zara, shop


def test_a_player_lists_the_shops_where_their_hero_stands_and_reads_one(town):
    client, mike, aria, _zara, shop = town
    assert expect(client.get(f"/api/heroes/{aria}/shops"), 200).json() == [{"id": shop, "key": "general", "name": "General Store"}]
    seen = expect(client.get(f"/api/heroes/{aria}/shops/{shop}"), 200).json()
    assert seen["shop"]["name"] == "General Store" and seen["gold"] == 500 and seen["sellable"] == []
    assert isinstance(seen["level"], int) and "wares" in seen


def test_buying_and_selling_over_http_move_gold_and_items(town):
    client, mike, aria, _zara, shop = town
    url = f"/api/heroes/{aria}/shops/{shop}"
    bought = expect(client.post(f"{url}/buy", json={"item": "herb", "qty": 2}, headers=mike), 200).json()
    assert bought == {"bought": 2, "not_fitting": 0, "cost": 20, "gold": 480}
    seen = expect(client.get(url), 200).json()
    assert [(each["position"], each["item"], each["qty"], each["each"]) for each in seen["sellable"]] == [(0, "herb", 2, 20)]
    sold = expect(client.post(f"{url}/sell", json={"position": 0, "qty": 2}, headers=mike), 200).json()
    assert sold == {"sold": 2, "paid": 40, "gold": 520}, "this game pays double for herbs"


def test_refusals_answer_409_and_a_hero_away_from_the_shop_finds_none(town):
    client, mike, aria, _zara, shop = town
    url = f"/api/heroes/{aria}/shops/{shop}"
    assert client.post(f"{url}/buy", json={"item": "crown", "qty": 1}, headers=mike).status_code == 409, "not for sale"
    assert client.post(f"{url}/buy", json={"item": "sword", "qty": 9}, headers=mike).status_code == 409, "equipment one at a time"
    assert client.post(f"{url}/sell", json={"position": 5, "qty": 1}, headers=mike).status_code == 409, "nothing there"

    async def walk_away(db):
        (await db.get(Hero, aria)).x = 7

    in_app_db(client, walk_away)
    assert expect(client.get(f"/api/heroes/{aria}/shops"), 200).json() == []
    expect(client.get(url), 404)
    assert client.post(f"{url}/buy", json={"item": "herb", "qty": 1}, headers=mike).status_code == 404


def test_the_calls_are_refused_for_the_wrong_player_and_a_missing_csrf_token(town):
    client, mike, aria, zara, shop = town
    client.post("/api/login", json={"username": "Zed", "password": PASSWORD})
    expect(client.get(f"/api/heroes/{aria}/shops"), 404)  # Zed cannot look through Aria's eyes
    mike = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    assert client.post(f"/api/heroes/{aria}/shops/{shop}/buy", json={"item": "herb", "qty": 1}).status_code == 403, "no CSRF token"
    assert client.post(f"/api/heroes/{zara}/shops/{shop}/buy", json={"item": "herb", "qty": 1}, headers=mike).status_code == 404, "not Mike's hero"


def test_the_calls_take_only_what_they_define(town):
    client, mike, aria, _zara, shop = town
    url = f"/api/heroes/{aria}/shops/{shop}"
    for path, body in (
        ("buy", {"item": "herb", "qty": 0}), ("buy", {"item": "herb", "qty": "2"}), ("buy", {"item": "", "qty": 1}),
        ("buy", {"item": "herb", "extra": 1}), ("buy", {"qty": 1}),
        ("sell", {"position": -1, "qty": 1}), ("sell", {"position": 0, "qty": 0}), ("sell", {"position": 0, "extra": 1}),
    ):
        assert client.post(f"{url}/{path}", json=body, headers=mike).status_code == 422, (path, body)
