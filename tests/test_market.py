"""The market as dialog activities: ``vend`` and ``hawk`` with the lists written in the NPC's text, and ``shop,key`` with the
game's ``Market`` hook; buying and selling, and the calls, on every database."""

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.economy import TeamGold
from terraforma.fights.build import hero_fighter
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import inventory, service
from terraforma.heroes.models import HeroEquipment
from terraforma.market import service as market
from terraforma.market.hooks import Market
from terraforma.npcs import service as npcs
from terraforma.npcs.hooks import Npcs
from terraforma.reach.hooks import Reach
from terraforma.npcs.script import ScriptError, parse
from terraforma.testing import in_app_db

from .helpers import expect

pytestmark = pytest.mark.anyio

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
NPCS, ECONOMY, RULES = Npcs(), TeamGold(), Rules()
REACH = Reach()
VEND = "Fresh stock.`vend,herb,12,potion,30,sword,150,Maybe later`\nBuy something?`ack`Come again.`jump,end`" "`label,Maybe later`Fine."
HAWK = "I buy things.`hawk,50,herb,20,Bye`\nWell?`ack`Done.`jump,end`" "`label,Bye`Bye."
SHOP = "Welcome.`shop,general,Bye`\nWell?`ack`Done.`jump,end`" "`label,Bye`Bye."


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


async def talking(db, hero, dialog):
    """The hero talks to a keeper next to them until the dialog waits on its shop; returns that prompt."""
    npc = await npcs.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x + 1, hero.y, dialog)
    await npcs.talk(db, NPCS, REACH, hero, npc.id)
    return await npcs.activity(db, NPCS, REACH, hero, market.COMMANDS)


async def holding(db, hero, key):
    return sum(stack.qty for stack, item in await inventory.stacks(db, hero) if item.key == key)


async def gold(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


async def buy(db, hero, prompt, key, qty=1, rules=None):
    return await market.buy(db, rules or Flat(), RULES, ECONOMY, hero, prompt, key, qty)


async def sell(db, hero, prompt, position, qty=1, rules=None):
    return await market.sell(db, rules or Flat(), RULES, ECONOMY, hero, prompt, position, qty)


# --- the tags --------------------------------------------------------------------------------------------------------

def test_the_shop_tag_takes_a_key_and_may_have_a_cancel_label():
    parse("`shop,general`Hi.")
    parse("`shop,general,away`Hi.`label,away`Bye.")
    for bad in ("`shop`", "`shop,a,b,c`", "`shop,general,nowhere`"):
        with pytest.raises(ScriptError):
            parse(bad)


# --- vend ------------------------------------------------------------------------------------------------------------

async def test_a_vend_sells_what_the_dialog_lists_at_the_dialog_prices(db):
    hero, team = await a_hero(db)
    prompt = await talking(db, hero, VEND)
    seen = await market.view(db, Flat(), RULES, ECONOMY, hero, prompt)
    assert [(ware["item"], ware["price"]) for ware in seen["wares"]] == [("herb", 12), ("potion", 30), ("sword", 150)]
    assert seen["sellable"] == [] and seen["gold"] == 500
    done = await buy(db, hero, prompt, "herb", 3)
    assert done == {"bought": 3, "not_fitting": 0, "cost": 36, "gold": 464}
    assert await holding(db, hero, "herb") == 3 and await gold(db, team) == 464


async def test_a_vend_does_not_sell_an_item_it_does_not_list_and_never_buys(db):
    hero, team = await a_hero(db)
    prompt = await talking(db, hero, VEND)
    with pytest.raises(market.ShopError, match="isn't for sale"):
        await buy(db, hero, prompt, "crown")
    await inventory.add_item(db, hero, "herb", 2)
    assert (await market.view(db, Flat(), RULES, ECONOMY, hero, prompt))["sellable"] == []
    with pytest.raises(market.ShopError, match="won't pay"):
        await sell(db, hero, prompt, 0)
    assert await gold(db, team) == 500


async def test_buying_charges_only_for_what_fits_and_refuses_what_costs_too_much(db):
    hero, team = await a_hero(db, team_gold=20)
    prompt = await talking(db, hero, VEND)
    with pytest.raises(market.ShopError, match="costs more"):
        await buy(db, hero, prompt, "herb", 2)
    assert await holding(db, hero, "herb") == 0 and await gold(db, team) == 20, "a refused purchase leaves nothing behind"
    with pytest.raises(market.ShopError, match="one at a time|how many"):
        await buy(db, hero, prompt, "sword", 2)
    with pytest.raises(market.ShopError, match="how many"):
        await buy(db, hero, prompt, "herb", 0)
    for _ in range(inventory.MAX_ITEMS):  # fills the pack with swords
        await inventory.add_item(db, hero, "sword", 1)
    team.gold = 1000
    await db.flush()
    with pytest.raises(market.ShopError, match="can't carry"):
        await buy(db, hero, prompt, "sword")
    assert await gold(db, team) == 1000


# --- hawk ------------------------------------------------------------------------------------------------------------

async def test_a_hawk_pays_the_listed_price_for_a_listed_item_and_the_margin_for_the_rest(db):
    hero, team = await a_hero(db)
    await inventory.add_item(db, hero, "herb", 4)
    await inventory.add_item(db, hero, "potion", 3)
    prompt = await talking(db, hero, HAWK)
    seen = await market.view(db, Flat(), RULES, ECONOMY, hero, prompt)
    assert seen["wares"] == []
    assert [(each["item"], each["each"]) for each in seen["sellable"]] == [("herb", 20), ("potion", 13)], "25 * 50% rounds half up"
    assert await sell(db, hero, prompt, 0, 4) == {"sold": 4, "paid": 80, "gold": 580}
    assert await holding(db, hero, "herb") == 0
    assert await sell(db, hero, prompt, 0, 2) == {"sold": 2, "paid": 26, "gold": 606}
    with pytest.raises(market.ShopError, match="how many"):
        await sell(db, hero, prompt, 0, 5)
    with pytest.raises(market.ShopError, match="doesn't have"):
        await sell(db, hero, prompt, 9)
    assert await gold(db, team) == 606


async def test_a_hawk_refuses_what_would_pay_nothing_and_takes_a_worn_item_off(db):
    hero, team = await a_hero(db)
    await inventory.add_item(db, hero, "keepsake", 1)
    prompt = await talking(db, hero, "`hawk,50,Bye`Hi.`label,Bye`Bye.")
    with pytest.raises(market.ShopError, match="won't pay"):
        await sell(db, hero, prompt, 0)
    assert await holding(db, hero, "keepsake") == 1, "nothing is thrown away for no gold"
    await inventory.add_item(db, hero, "sword", 1)
    stack = next(stack for stack, item in await inventory.stacks(db, hero) if item.key == "sword")
    db.add(HeroEquipment(hero_id=hero.id, slot="hand", hero_item_id=stack.id))
    await db.flush()
    done = await sell(db, hero, prompt, stack.position)
    assert done["paid"] == 50
    assert await db.scalar(select(func.count()).select_from(HeroEquipment)) == 0


# --- shop ------------------------------------------------------------------------------------------------------------

async def test_a_shop_stocks_what_the_markets_level_allows_and_sells_back_at_the_markets_rate(db):
    hero, team = await a_hero(db)
    prompt = await talking(db, hero, SHOP)
    seen = await market.view(db, Flat(), RULES, ECONOMY, hero, prompt)
    assert seen["level"] == 1000
    assert [ware["item"] for ware in seen["wares"]] == ["herb", "potion", "sword"], "cheapest first, nothing over the level, nothing free"
    assert (await buy(db, hero, prompt, "potion", 2))["cost"] == 50
    assert await holding(db, hero, "potion") == 2
    with pytest.raises(market.ShopError, match="isn't for sale"):
        await buy(db, hero, prompt, "crown")
    assert (await sell(db, hero, prompt, 0, 2))["paid"] == 38, "75% of 25 rounds half up to 19 each"
    assert await gold(db, team) == 488


async def test_the_default_level_is_the_acting_partys_pxp(db):
    hero, _team = await a_hero(db)
    prompt = await talking(db, hero, SHOP)
    seen = await market.view(db, Market(), RULES, ECONOMY, hero, prompt)
    assert seen["level"] == int(RULES.pxp(await hero_fighter(db, hero)))


async def test_a_game_can_price_one_item_differently_for_a_quest(db):
    hero, _team = await a_hero(db)
    await inventory.add_item(db, hero, "herb", 2)
    prompt = await talking(db, hero, SHOP)
    assert (await sell(db, hero, prompt, 0, 2, Herbalist()))["paid"] == 40, "the area needs herbs: 200% of 10 each"


# --- from the conversation only --------------------------------------------------------------------------------------

async def test_no_shop_without_a_conversation_waiting_on_one(db):
    hero, _team = await a_hero(db)
    with pytest.raises(npcs.NpcError, match="not in a conversation"):
        await npcs.activity(db, NPCS, REACH, hero, market.COMMANDS)
    npc = await npcs.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x + 1, hero.y, "Hello.`ack`Bye.")
    await npcs.talk(db, NPCS, REACH, hero, npc.id)
    with pytest.raises(npcs.NpcError, match="nothing to do"):
        await npcs.activity(db, NPCS, REACH, hero, market.COMMANDS)


async def test_walking_away_ends_the_shopping(db):
    hero, _team = await a_hero(db)
    await talking(db, hero, VEND)
    hero.x += 5
    with pytest.raises(npcs.NpcError):
        await npcs.activity(db, NPCS, REACH, hero, market.COMMANDS)
    with pytest.raises(npcs.NpcError, match="not in a conversation"):
        await npcs.activity(db, NPCS, REACH, hero, market.COMMANDS)


# --- the calls -------------------------------------------------------------------------------------------------------

def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def test_the_calls_shop_from_the_conversation(app_client):
    client = app_client
    in_app_db(client, lambda db: load_content(db, SEED))
    headers = sign_in(client, "Mike")
    hero = expect(client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=headers), 201).json()["id"]

    async def set_up(db):
        row = await db.get(service.Hero, hero)
        row.gold = 100  # a hero on no team spends their own gold
        return (await npcs.place_npc(db, "keeper", "Keeper", row.map_id, row.x + 1, row.y, VEND)).id

    npc = in_app_db(client, set_up)
    shop = f"/api/heroes/{hero}/dialog/shop"
    assert client.get(shop).status_code == 409, "not talking to anyone"
    expect(client.post(f"/api/heroes/{hero}/npcs/{npc}/talk", json={}, headers=headers), 200)
    seen = expect(client.get(shop), 200).json()
    assert seen["command"] == "vend" and [ware["item"] for ware in seen["wares"]] == ["herb", "potion", "sword"]
    assert client.post(f"{shop}/buy", json={"item": "herb", "qty": 1}).status_code == 403, "no CSRF token"
    assert client.post(f"{shop}/buy", json={"item": "herb", "price": 1}, headers=headers).status_code == 422, "a price is never the browser's to name"
    assert client.post(f"{shop}/buy", json={"item": "crown"}, headers=headers).status_code == 409
    done = expect(client.post(f"{shop}/buy", json={"item": "herb", "qty": 2}, headers=headers), 200).json()
    assert done["bought"] == 2 and done["cost"] == 24
    assert client.post(f"{shop}/sell", json={"position": 0}, headers=headers).status_code == 409, "a vend never buys"
    expect(client.post(f"/api/heroes/{hero}/dialog/next", json={"choice": None}, headers=headers), 200)
    assert client.get(shop).status_code == 409, "the conversation moved on"
