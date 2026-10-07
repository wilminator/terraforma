"""Trading: gold and items between heroes, who may trade (the game's policy), the ledger and the calls, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.economy import Economy, HeroGold, TeamGold
from terraforma.game import Game
from terraforma.heroes import inventory, service
from terraforma.heroes.models import Hero, HeroItem
from terraforma.parties import service as parties
from terraforma import testing
from terraforma.testing import in_app_db
from terraforma.trading import policy
from terraforma.trading import service as trading
from terraforma.trading.models import TradeRecord

from .helpers import expect

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}],
    "items": [
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 30}},
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"], "stat_bonus": {"Strength": 3}},
    ],
}
WHATEVER = policy.Trade("gold", amount=1)


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the first account)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def team_with(db, owner, team_name, hero_names):
    team = await service.create_team(db, owner, team_name)
    heroes = []
    for name in hero_names:
        hero = await service.create_hero(db, owner, name, "fighter")
        await service.add_to_team(db, owner, team.id, hero.id)
        heroes.append(hero)
    return team, heroes


async def stock(db, hero, key, qty=1):
    assert await inventory.add_item(db, hero, key, qty) == 0


async def holding(db, hero, key):
    return sum(stack.qty for stack, item in await inventory.stacks(db, hero) if item.key == key)


async def ledger_count(db):
    return await db.scalar(select(func.count()).select_from(TradeRecord))


class Open(Economy):
    """Gold on the hero, and anyone may trade with anyone."""

    trade_policy = policy.Anyone()
    purse = HeroGold.purse
    credit_team = HeroGold.credit_team


# --- who may trade -----------------------------------------------------------------------------------------------

@anyio
async def test_teammates_trade_by_default_and_no_one_else_does(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    _other, (cole,) = await team_with(db, mike, "Rearguard", ["Cole"])
    loner = await service.create_hero(db, mike, "Loner", "fighter")
    economy = TeamGold()
    assert await economy.can_trade(db, aria, bram, WHATEVER)
    assert not await economy.can_trade(db, aria, cole, WHATEVER)
    assert not await economy.can_trade(db, aria, loner, WHATEVER)
    assert not await economy.can_trade(db, loner, loner, WHATEVER), "a hero on no team trades with no one"


@anyio
async def test_the_stock_policies(db):
    mike, zed = await account(db), None
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    one, (aria,) = await team_with(db, mike, "One", ["Aria"])
    two, (bram,) = await team_with(db, mike, "Two", ["Bram"])
    three, (zara,) = await team_with(db, zed, "Three", ["Zara"])
    together = await parties.create_party(db, one.id, 20)
    await parties.join_party(db, together.id, two.id, 20)  # Aria's and Bram's teams share a party; Zara's is outside it
    economy = TeamGold()

    async def allowed(chosen, giver, receiver):
        return await chosen.allows(economy, db, giver, receiver)

    assert not await allowed(policy.NoOne(), aria, aria) and not await allowed(policy.NoOne(), aria, bram)
    assert await allowed(policy.Anyone(), aria, zara)
    assert not await allowed(policy.WithinTeam(), aria, bram)
    assert await allowed(policy.WithinParty(), aria, bram) and await allowed(policy.WithinParty(), bram, aria)
    assert not await allowed(policy.WithinParty(), aria, zara)
    assert not await allowed(policy.Related(), aria, zara), "nobody is related until a game says so"
    assert await allowed(policy.AnyOf(policy.WithinTeam(), policy.WithinParty()), aria, bram)
    assert not await allowed(policy.AnyOf(policy.NoOne(), policy.WithinTeam()), aria, zara)


@anyio
async def test_a_game_can_say_who_is_related(db):
    mike = await account(db)
    _t, (aria,) = await team_with(db, mike, "One", ["Aria"])
    _u, (cole,) = await team_with(db, mike, "Two", ["Cole"])

    class Friends(Economy):
        trade_policy = policy.AnyOf(policy.WithinTeam(), policy.Related())

        async def related(self, session, giver, receiver):
            return {giver.name, receiver.name} == {"Aria", "Cole"}

    economy = Friends()
    assert await economy.can_trade(db, aria, cole, WHATEVER) and await economy.can_trade(db, cole, aria, WHATEVER)
    assert not await TeamGold().can_trade(db, aria, cole, WHATEVER)


# --- gold ----------------------------------------------------------------------------------------------------------------

@anyio
async def test_gold_moves_between_purses_and_is_recorded(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    economy = Open()
    await economy.credit(db, aria, 50)
    assert await trading.give_gold(db, economy, aria, bram.id, 20) == 30
    assert await economy.balance(db, bram) == 20 and await economy.balance(db, aria) == 30
    row = await db.scalar(select(TradeRecord))
    assert (row.giver_id, row.receiver_id, row.kind, row.qty, row.item_key) == (aria.id, bram.id, "gold", 20, "")


@anyio
async def test_more_gold_than_there_is_moves_nothing(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    economy = Open()
    await economy.credit(db, aria, 5)
    with pytest.raises(trading.TradeError, match="not enough gold"):
        await trading.give_gold(db, economy, aria, bram.id, 6)
    assert await economy.balance(db, aria) == 5 and await economy.balance(db, bram) == 0 and await ledger_count(db) == 0


@anyio
async def test_teammates_who_share_a_purse_have_nothing_to_give_each_other(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    await TeamGold().credit(db, aria, 10)
    with pytest.raises(trading.TradeError, match="share one purse"):
        await trading.give_gold(db, TeamGold(), aria, bram.id, 5)
    assert await TeamGold().balance(db, aria) == 10 and await ledger_count(db) == 0


@anyio
async def test_a_gift_is_at_least_one_gold(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    for amount in (0, -3, trading.MAX_GOLD + 1):
        with pytest.raises(trading.TradeError):
            await trading.give_gold(db, Open(), aria, bram.id, amount)


# --- items ---------------------------------------------------------------------------------------------------------------

@anyio
async def test_an_item_moves_from_one_pack_to_the_other_and_is_recorded(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    await stock(db, aria, "potion", 5)
    await trading.give_item(db, TeamGold(), aria, bram.id, 0, 3)
    assert await holding(db, aria, "potion") == 2 and await holding(db, bram, "potion") == 3
    await trading.give_item(db, TeamGold(), aria, bram.id, 0, 2)  # the rest: the stack is gone
    assert await holding(db, aria, "potion") == 0 and await holding(db, bram, "potion") == 5
    rows = (await db.scalars(select(TradeRecord).order_by(TradeRecord.id))).all()
    assert [(row.kind, row.item_key, row.qty) for row in rows] == [("item", "potion", 3), ("item", "potion", 2)]


@anyio
async def test_only_what_is_there_and_not_worn_can_be_given(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    await stock(db, aria, "potion", 2)
    await stock(db, aria, "sword")
    await inventory.equip(db, aria, 1)
    for position, qty, message in ((5, 1, "nothing in that position"), (0, 3, "you have 2"), (0, 0, "you have 2"), (1, 1, "take it off first")):
        with pytest.raises(trading.TradeError, match=message):
            await trading.give_item(db, TeamGold(), aria, bram.id, position, qty)
    assert await holding(db, aria, "potion") == 2 and await holding(db, bram, "potion") == 0 and await ledger_count(db) == 0


@anyio
async def test_a_full_pack_refuses_the_gift_and_nothing_moves(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    for _ in range(inventory.MAX_ITEMS):
        await stock(db, bram, "sword")  # gear never stacks: twelve swords fill the pack
    await stock(db, aria, "potion", 4)
    with pytest.raises(trading.TradeError, match="no room"):
        await trading.give_item(db, TeamGold(), aria, bram.id, 0, 4)
    assert await holding(db, aria, "potion") == 4 and await holding(db, bram, "potion") == 0
    assert await db.scalar(select(func.count()).select_from(HeroItem).where(HeroItem.hero_id == bram.id)) == inventory.MAX_ITEMS
    assert await ledger_count(db) == 0


@anyio
async def test_a_refusal_reads_the_same_whether_the_hero_exists_or_not(db):
    mike = await account(db)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    _team, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    _other, (zara,) = await team_with(db, zed, "Rivals", ["Zara"])
    await stock(db, aria, "potion")
    for receiver in (zara.id, 999_999, aria.id):  # another team's hero, no hero at all, oneself
        with pytest.raises(trading.Refused) as caught:
            await trading.give_item(db, TeamGold(), aria, receiver, 0, 1)
        assert str(caught.value) == trading.REFUSED
        with pytest.raises(trading.Refused):
            await trading.give_gold(db, TeamGold(), aria, receiver, 1)
    assert await holding(db, aria, "potion") == 1 and await ledger_count(db) == 0


# --- the ledger ------------------------------------------------------------------------------------------------------------

@anyio
async def test_the_ledger_lists_both_directions_newest_first_and_outlives_the_heroes(db):
    mike = await account(db)
    _team, (aria, bram) = await team_with(db, mike, "Vanguard", ["Aria", "Bram"])
    economy = Open()
    await economy.credit(db, aria, 10)
    await stock(db, bram, "potion", 2)
    await trading.give_gold(db, economy, aria, bram.id, 4)
    await trading.give_item(db, economy, bram, aria.id, 0, 1)
    mine = await trading.history(db, aria)
    assert [(row["direction"], row["other"], row["kind"], row["item"], row["qty"]) for row in mine] == [
        ("received", "Bram", "item", "potion", 1), ("gave", "Bram", "gold", None, 4),
    ]
    assert [row["direction"] for row in await trading.history(db, bram)] == ["gave", "received"]
    assert len(await trading.history(db, aria, limit=1)) == 1
    await service.delete_hero(db, mike, bram.id)
    assert (await trading.history(db, aria))[0]["other"] == "Bram", "the history stays readable after a hero is deleted"


# --- the calls ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path, request):
    """The game the app serves: the default economy, or the one a test parametrizes in (``indirect``)."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    economy = getattr(request, "param", None)
    return Game(name="Test Game", seed_dir=seed_dir, **({"economy": economy} if economy else {}))


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def make_hero(client, headers, name):
    return testing.make_hero(client, headers, name)["id"]


def stock_over_http(client, hero_id, key, qty=1):
    async def work(db):
        return await inventory.add_item(db, await db.get(Hero, hero_id), key, qty)

    assert in_app_db(client, work) == 0


@pytest.fixture
def pair(app_client):
    """Mike's Aria and Bram on one team, and Zed's Zara on his own."""
    mike = sign_in(app_client, "Mike")
    aria, bram = (member["hero_id"] for member in testing.make_team(app_client, mike, "Vanguard", heroes=("Aria", "Bram"))["members"])
    zed = sign_in(app_client, "Zed")
    zara = make_hero(app_client, zed, "Zara")
    # (sign_in left Zed logged in: act as Mike again for the calls below)
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    return app_client, mike, aria, bram, zara


def test_a_hero_gives_an_item_to_a_teammate(pair):
    client, headers, aria, bram, _zara = pair
    stock_over_http(client, aria, "potion", 4)
    result = expect(client.post(f"/api/heroes/{aria}/give-item", json={"to_hero_id": bram, "position": 0, "qty": 3}, headers=headers), 200).json()
    assert [(row["item"], row["qty"]) for row in result["items"]] == [("potion", 1)]
    theirs = expect(client.get(f"/api/heroes/{bram}/inventory"), 200).json()
    assert [(row["item"], row["qty"]) for row in theirs["items"]] == [("potion", 3)]
    ledger = expect(client.get(f"/api/heroes/{aria}/trades"), 200).json()
    assert [(row["direction"], row["other"], row["item"], row["qty"]) for row in ledger] == [("gave", "Bram", "potion", 3)]


def test_gold_between_teammates_who_share_the_teams_purse_is_refused(pair):
    client, headers, aria, bram, _zara = pair
    response = expect(client.post(f"/api/heroes/{aria}/give-gold", json={"to_hero_id": bram, "amount": 1}, headers=headers), 409)
    assert "share one purse" in response.json()["detail"]


def test_another_teams_hero_and_a_missing_hero_are_refused_alike(pair):
    client, headers, aria, _bram, zara = pair
    stock_over_http(client, aria, "potion")
    answers = [
        expect(client.post(f"/api/heroes/{aria}/give-item", json={"to_hero_id": other, "position": 0}, headers=headers), 404).json()
        for other in (zara, 999_999)
    ]
    assert answers[0] == answers[1] == {"detail": trading.REFUSED}


def test_only_the_owner_gives_and_only_with_the_csrf_token(pair):
    client, headers, aria, bram, zara = pair
    stock_over_http(client, aria, "potion")
    body = {"to_hero_id": bram, "position": 0}
    expect(client.post(f"/api/heroes/{aria}/give-item", json=body), 403)  # no token
    assert client.post(f"/api/heroes/{zara}/give-item", json={**body, "to_hero_id": aria}, headers=headers).status_code == 404  # not Mike's hero
    client.post("/api/logout", headers=headers)
    assert client.post(f"/api/heroes/{aria}/give-item", json=body, headers=headers).status_code in (401, 403)


def test_the_calls_take_only_what_they_define(pair):
    client, headers, aria, bram, _zara = pair
    for url, body in (
        (f"/api/heroes/{aria}/give-gold", {"to_hero_id": bram, "amount": 0}),
        (f"/api/heroes/{aria}/give-gold", {"to_hero_id": bram, "amount": 1, "memo": "hi"}),
        (f"/api/heroes/{aria}/give-item", {"to_hero_id": 0, "position": 0}),
        (f"/api/heroes/{aria}/give-item", {"to_hero_id": bram, "position": 0, "qty": 0}),
        (f"/api/heroes/{aria}/give-item", {"to_hero_id": bram}),
    ):
        expect(client.post(url, json=body, headers=headers), 422)


def test_giving_is_rate_limited_per_account(pair, monkeypatch):
    client, headers, aria, bram, _zara = pair
    monkeypatch.setattr(ratelimit, "TRADE_BY_ACCOUNT", ratelimit.Limit("trade", 2, 3600))
    stock_over_http(client, aria, "potion", 5)
    body = {"to_hero_id": bram, "position": 0}
    expect(client.post(f"/api/heroes/{aria}/give-item", json=body, headers=headers), 200)
    expect(client.post(f"/api/heroes/{aria}/give-item", json=body, headers=headers), 200)
    limited = expect(client.post(f"/api/heroes/{aria}/give-item", json=body, headers=headers), 429)
    assert "Retry-After" in limited.headers


@pytest.mark.parametrize("game", [Open()], indirect=True)
def test_an_open_economy_lets_anyone_trade(app_client):
    mike, zed = sign_in(app_client, "Mike"), sign_in(app_client, "Zed")
    zara = make_hero(app_client, zed, "Zara")
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    aria = make_hero(app_client, mike, "Aria")
    stock_over_http(app_client, aria, "potion", 2)
    expect(app_client.post(f"/api/heroes/{aria}/give-item", json={"to_hero_id": zara, "position": 0, "qty": 2}, headers=mike), 200)

    async def what_zara_has(db):
        return [(item.key, stack.qty) for stack, item in await inventory.stacks(db, await db.get(Hero, zara))]

    assert in_app_db(app_client, what_zara_has) == [("potion", 2)]  # (her inventory is hers to look at, not Mike's)
