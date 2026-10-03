"""Challenge Tokens: one balance per account, the ledger, what a fight pays (an admin only as a player), the game's daily and purse
caps, and the server-to-server purchase call, on every database."""

import pytest
from sqlalchemy import select, text

from terraforma.accounts.service import create_account
from terraforma.db.migrate import downgrade, upgrade
from terraforma.fights import store
from terraforma.fights.rules import Rules
from terraforma.models import Account
from terraforma.testing import forget_schema, in_app_db
from terraforma.challenge import service as tokens
from terraforma.challenge.models import ChallengeEntry as TokenEntry

from .helpers import expect
from .test_fight_store import PASSWORD, a_team_fights_a_rat, play_to_the_end

pytestmark = pytest.mark.anyio


class Pays(Rules):
    """A game that pays 5 tokens to every fighter, hero or monster, when a fight ends."""

    def challenge_earned(self, fight, address):
        return 5


async def finished_fight(db):
    hero, record = await a_team_fights_a_rat(db)
    await play_to_the_end(db, record)
    return await db.get(Account, hero.account_id), record


async def test_the_default_rules_pay_no_tokens(db):
    account, record = await finished_fight(db)
    await store.apply_results(db, record, Rules())
    assert await tokens.balance(db, account.id) == 0
    assert (await db.scalars(select(TokenEntry))).all() == []


async def test_a_fight_pays_its_player_once_and_the_ledger_adds_up(db):
    account, record = await finished_fight(db)
    await store.apply_results(db, record, Pays())
    assert await tokens.balance(db, account.id) == 5, "the hero's 5; the rat is no account"
    await store.apply_results(db, record, Pays())
    assert await tokens.balance(db, account.id) == 5, "saving the result twice pays once"
    entries = (await db.scalars(select(TokenEntry))).all()
    assert [(each.account_id, each.amount, each.reason, each.fight_id) for each in entries] == [(account.id, 5, "fight", record.id)]
    assert await tokens.audit(db, account.id)


async def test_an_admin_earns_as_a_player_but_not_for_a_fight_they_were_not_in(db):
    account, record = await finished_fight(db)
    other = await create_account(db, "Watcher", PASSWORD, email="watcher@example.com", confirmed=True)
    await tokens.set_admin(db, account.id)
    await tokens.set_admin(db, other.id)
    await store.apply_results(db, record, Pays())
    assert await tokens.balance(db, account.id) == 5, "fighting as a player, on the same terms as anyone"
    assert await tokens.balance(db, other.id) == 0, "an admin who only watched earns nothing"


async def test_a_negative_answer_pays_nothing(db):
    class Takes(Rules):
        def challenge_earned(self, fight, address):
            return -3

    account, record = await finished_fight(db)
    await store.apply_results(db, record, Takes())
    assert await tokens.balance(db, account.id) == 0


async def test_a_spend_is_recorded_and_cannot_overdraw(db):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    assert await tokens.change(db, account.id, 10, "gift")
    assert await tokens.change(db, account.id, -4, "shop")
    assert await tokens.balance(db, account.id) == 6
    with pytest.raises(tokens.NotEnoughTokens):
        await tokens.change(db, account.id, -7, "shop")
    assert await tokens.balance(db, account.id) == 6, "a refused spend changes nothing"
    assert len((await db.scalars(select(TokenEntry))).all()) == 2, "and leaves no ledger row"
    assert await tokens.audit(db, account.id)


async def test_nobody_is_an_admin_until_a_game_says_so(db):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    assert account.is_admin is False
    await tokens.set_admin(db, account.id)
    await db.refresh(account)
    assert account.is_admin is True
    await tokens.set_admin(db, account.id, False)
    await db.refresh(account)
    assert account.is_admin is False


# --- the game's caps ------------------------------------------------------------------------------------------------------------------

class Capped(Pays):
    """Pays 5 to every fighter, with the limits the test sets on the class."""

    daily: int | None = None
    purse: int | None = None

    def challenge_daily_cap(self, account_id):
        return self.daily

    def challenge_purse_cap(self, account_id):
        return self.purse


def capped(daily=None, purse=None):
    rules = Capped()
    rules.daily, rules.purse = daily, purse
    return rules


async def test_there_are_no_caps_unless_a_game_sets_them():
    assert Rules().challenge_daily_cap(1) is None and Rules().challenge_purse_cap(1) is None


async def test_the_daily_cap_cuts_what_a_fight_pays_and_resets_at_midnight(db, later):
    later(0)
    account, record = await finished_fight(db)
    await tokens.change(db, account.id, 4, "fight", fight_id=None)  # earlier today, from another fight
    assert await tokens.earned_today(db, account.id) == 4
    await store.apply_results(db, record, capped(daily=6))
    assert await tokens.balance(db, account.id) == 6, "the 5 was cut to the 2 left of the day's 6"
    assert await tokens.earned_today(db, account.id) == 6
    assert await tokens.audit(db, account.id)
    later(24 * 60 * 60)  # tomorrow
    assert await tokens.earned_today(db, account.id) == 0
    await store.apply_results(db, record, capped(daily=6))
    assert await tokens.balance(db, account.id) == 6, "a fight that was paid (cut or not) is never paid again"


async def test_a_fight_cut_to_nothing_still_counts_as_paid(db, later):
    later(0)
    account, record = await finished_fight(db)
    await tokens.change(db, account.id, 3, "fight")
    await store.apply_results(db, record, capped(daily=3))
    assert await tokens.balance(db, account.id) == 3
    later(24 * 60 * 60)
    await store.apply_results(db, record, capped(daily=3))
    assert await tokens.balance(db, account.id) == 3, "the empty pay was recorded, so tomorrow does not pay it"


async def test_purchases_and_spends_do_not_use_up_the_daily_cap(db, later):
    later(0)
    account, record = await finished_fight(db)
    await tokens.purchase(db, capped(daily=5), account.id, 100, "buy-0001")
    await tokens.change(db, account.id, -10, "shop")
    assert await tokens.earned_today(db, account.id) == 0
    await store.apply_results(db, record, capped(daily=5))
    assert await tokens.balance(db, account.id) == 95, "100 bought, 10 spent, and the fight's 5 still fit the day"


async def test_the_purse_cap_cuts_earnings_to_what_fits(db):
    account, record = await finished_fight(db)
    await tokens.change(db, account.id, 8, "gift")
    await store.apply_results(db, record, capped(purse=10))
    assert await tokens.balance(db, account.id) == 10, "5 earned, 2 fit"


async def test_a_purse_over_its_cap_earns_nothing_but_can_still_spend(db):
    account, record = await finished_fight(db)
    await tokens.change(db, account.id, 20, "gift")  # the game lowered the cap since
    await store.apply_results(db, record, capped(purse=10))
    assert await tokens.balance(db, account.id) == 20
    assert await tokens.change(db, account.id, -15, "shop", purse_cap=10)
    assert await tokens.balance(db, account.id) == 5


async def test_change_refuses_a_credit_over_the_purse_cap_and_records_nothing(db):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    await tokens.change(db, account.id, 8, "gift")
    with pytest.raises(tokens.PurseFull):
        await tokens.change(db, account.id, 3, "gift", purse_cap=10)
    assert await tokens.balance(db, account.id) == 8
    assert len((await db.scalars(select(TokenEntry))).all()) == 1
    assert await tokens.audit(db, account.id)


# --- purchases ------------------------------------------------------------------------------------------------------------------------

async def test_a_purchase_is_credited_once_however_often_it_is_sent(db):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    first = await tokens.purchase(db, Rules(), account.id, 50, "order-1234")
    again = await tokens.purchase(db, Rules(), account.id, 50, "order-1234")
    assert (first.amount, first.balance, first.duplicate) == (50, 50, False)
    assert (again.amount, again.balance, again.duplicate) == (50, 50, True)
    assert await tokens.balance(db, account.id) == 50
    entries = (await db.scalars(select(TokenEntry))).all()
    assert [(each.amount, each.reason, each.idempotency_key, each.fight_id) for each in entries] == [(50, "purchase", "order-1234", None)]
    assert await tokens.audit(db, account.id)


async def test_a_key_cannot_be_reused_for_another_purchase(db):
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    await tokens.purchase(db, Rules(), mike.id, 50, "order-1234")
    with pytest.raises(tokens.KeyReused):
        await tokens.purchase(db, Rules(), mike.id, 60, "order-1234")
    with pytest.raises(tokens.KeyReused):
        await tokens.purchase(db, Rules(), zed.id, 50, "order-1234")
    assert (await tokens.balance(db, mike.id), await tokens.balance(db, zed.id)) == (50, 0)


async def test_a_purchase_that_does_not_fit_the_purse_is_refused_whole(db):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    await tokens.purchase(db, capped(purse=100), account.id, 90, "order-0001")
    with pytest.raises(tokens.PurseFull):
        await tokens.purchase(db, capped(purse=100), account.id, 11, "order-0002")
    assert await tokens.balance(db, account.id) == 90, "nothing of it was credited"
    await tokens.purchase(db, capped(purse=100), account.id, 10, "order-0003")
    assert await tokens.balance(db, account.id) == 100


async def test_a_purchase_needs_an_account_and_a_positive_amount(db):
    with pytest.raises(tokens.UnknownAccount):
        await tokens.purchase(db, Rules(), 999, 5, "order-0001")
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    for amount in (0, -5):
        with pytest.raises(tokens.ChallengeError):
            await tokens.purchase(db, Rules(), account.id, amount, "order-0002")


# --- the server-to-server call ----------------------------------------------------------------------------------------------------------

SECRET = "shop-backend-secret-0123456789abcdef"
URL = "/api/server/challenge/purchase"


def a_player(client, username="Mike"):
    return in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True)).id


def shop(client, secret=SECRET):
    client.app.state.settings = client.app.state.settings.model_copy(update={"challenge_purchase_secret": secret})
    return {"Authorization": f"Bearer {SECRET}"}


def test_the_call_does_not_exist_until_a_game_sets_the_secret(app_client):
    account_id = a_player(app_client)
    reply = app_client.post(URL, json={"account_id": account_id, "amount": 5, "key": "order-0001"}, headers={"Authorization": f"Bearer {SECRET}"})
    assert reply.status_code == 404


def test_the_call_credits_a_purchase_once(app_client):
    account_id = a_player(app_client)
    headers = shop(app_client)
    body = {"account_id": account_id, "amount": 25, "key": "order-0001"}
    assert expect(app_client.post(URL, json=body, headers=headers), 200).json() == {"credited": 25, "balance": 25, "duplicate": False}
    assert expect(app_client.post(URL, json=body, headers=headers), 200).json() == {"credited": 25, "balance": 25, "duplicate": True}
    assert in_app_db(app_client, lambda db: tokens.balance(db, account_id)) == 25


def test_the_call_needs_the_secret(app_client):
    account_id = a_player(app_client)
    shop(app_client)
    body = {"account_id": account_id, "amount": 25, "key": "order-0001"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}, {"Authorization": f"Basic {SECRET}"}):
        reply = app_client.post(URL, json=body, headers=headers)
        assert reply.status_code == 401 and reply.headers["WWW-Authenticate"] == "Bearer"
    assert in_app_db(app_client, lambda db: tokens.balance(db, account_id)) == 0


def test_a_player_logged_in_cannot_use_the_call_without_the_secret(app_client):
    account_id = a_player(app_client)
    shop(app_client)
    app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD})
    reply = app_client.post(URL, json={"account_id": account_id, "amount": 25, "key": "order-0001"})
    assert reply.status_code == 401, "a session cookie is no use here"


def test_the_call_answers_unknown_accounts_and_conflicts(app_client):
    account_id = a_player(app_client)
    headers = shop(app_client)
    assert app_client.post(URL, json={"account_id": 9999, "amount": 5, "key": "order-0001"}, headers=headers).status_code == 404
    app_client.post(URL, json={"account_id": account_id, "amount": 5, "key": "order-0002"}, headers=headers)
    assert app_client.post(URL, json={"account_id": account_id, "amount": 6, "key": "order-0002"}, headers=headers).status_code == 409


def test_the_call_refuses_bad_bodies(app_client):
    account_id = a_player(app_client)
    headers = shop(app_client)
    good = {"account_id": account_id, "amount": 5, "key": "order-0001"}
    for bad in (
        {**good, "amount": 0},
        {**good, "amount": -1},
        {**good, "amount": "5"},
        {**good, "key": "short"},
        {**good, "key": "has spaces in it!"},
        {**good, "extra": 1},
        {"amount": 5, "key": "order-0001"},
    ):
        assert app_client.post(URL, json=bad, headers=headers).status_code == 422, bad


def test_the_call_is_rate_limited_by_address(app_client, monkeypatch):
    from terraforma.accounts import ratelimit

    account_id = a_player(app_client)
    headers = shop(app_client)
    monkeypatch.setattr(ratelimit, "CHALLENGE_PURCHASE_BY_ADDRESS", ratelimit.Limit("challenge-purchase", 2, 15 * 60))
    body = {"account_id": account_id, "amount": 5, "key": "order-0001"}
    assert app_client.post(URL, json=body, headers=headers).status_code == 200
    assert app_client.post(URL, json=body, headers=headers).status_code == 200
    assert app_client.post(URL, json=body, headers=headers).status_code == 429


def test_a_secret_must_be_long_or_empty():
    from pydantic import ValidationError

    from terraforma.settings import Settings

    assert Settings(session_secret="x" * 32).challenge_purchase_secret == "", "off by default"
    assert Settings(session_secret="x" * 32, challenge_purchase_secret=SECRET).challenge_purchase_secret == SECRET
    with pytest.raises(ValidationError):
        Settings(session_secret="x" * 32, challenge_purchase_secret="too short")


# --- the migration ------------------------------------------------------------------------------------------------------------------

async def test_the_migration_carries_balances_and_the_ledger_both_ways(db, engine, database_url):
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    await tokens.change(db, account.id, 10, "gift")
    await tokens.change(db, account.id, -4, "shop")
    await db.commit()
    forget_schema(database_url)  # the tables are about to change: the next test rebuilds them
    await downgrade(engine, "0028")
    async with engine.connect() as connection:
        old = (await connection.execute(text("SELECT balance FROM token_balances WHERE account_id = :a"), {"a": account.id})).scalar()
        old_rows = (await connection.execute(text("SELECT amount, reason FROM token_ledger ORDER BY id"))).all()
    assert old == 6 and [tuple(row) for row in old_rows] == [(10, "gift"), (-4, "shop")]
    await upgrade(engine)
    async with engine.connect() as connection:
        new = (await connection.execute(text("SELECT balance FROM challenge_balances WHERE account_id = :a"), {"a": account.id})).scalar()
        new_rows = (await connection.execute(text("SELECT amount, reason FROM challenge_ledger ORDER BY id"))).all()
    assert new == 6 and [tuple(row) for row in new_rows] == [(10, "gift"), (-4, "shop")]
