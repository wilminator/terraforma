"""The store's grant: Challenge Tokens and items put on an account by the game's store, behind the server-to-server call, with the
game's rule having the final say, on every database."""

import pytest
from sqlalchemy import select

from terraforma.accounts.service import create_account
from terraforma.challenge import service as tokens
from terraforma.challenge.models import ChallengeEntry
from terraforma.content.models import Item
from terraforma.fights.rules import Rules
from terraforma.oidc import service as oidc_service
from terraforma.storegrant import service
from terraforma.storegrant.models import StoreGrant
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import PASSWORD

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

SECRET = "store-backend-secret-0123456789abcdef"
URL = "/api/server/store/grant"


def an_item(key="potion", active=True):
    return Item(key=key, name=key.title(), active=active, stat_bonus={}, stat_percent={}, use_presentation={}, fight_presentation={})


async def a_player(db, username="Mike", email=True):
    account = await create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True)
    db.add_all([an_item("potion"), an_item("ether"), an_item("retired", active=False)])
    await db.flush()
    return account


class Says(Rules):
    """A game whose rule refuses a grant for the reason it is told."""

    def __init__(self, no=None, purse=None):
        self.no, self.purse = no, purse
        self.asked = []

    def store_grant(self, account_id, tokens, items, message):
        self.asked.append((account_id, tokens, items, message))
        return self.no

    def challenge_purse_cap(self, account_id):
        return self.purse


async def test_the_default_rule_grants_anything():
    assert Rules().store_grant(1, 5, {"potion": 1}, "hello") is None


async def test_a_grant_credits_tokens_and_puts_items_in_the_parcel(db):
    mike = await a_player(db)
    done = await service.grant(db, Rules(), mike.id, 50, {"potion": 3, "ether": 1}, "Thanks!", "order-0001")
    assert (done.balance, done.duplicate) == (50, False)
    assert await tokens.balance(db, mike.id) == 50 and await tokens.audit(db, mike.id)
    assert await service.parcel(db, mike.id) == {"potion": 3, "ether": 1}
    entry = (await db.scalars(select(ChallengeEntry))).one()
    assert (entry.account_id, entry.amount, entry.reason, entry.idempotency_key) == (mike.id, 50, "store", "store:order-0001")
    await service.grant(db, Rules(), mike.id, 0, {"potion": 2}, "", "order-0002")
    assert await service.parcel(db, mike.id) == {"potion": 5, "ether": 1}, "items add up"


async def test_a_grant_can_take_tokens_back_but_not_below_zero(db):
    mike = await a_player(db)
    await service.grant(db, Rules(), mike.id, 30, {}, "", "order-0001")
    assert (await service.grant(db, Rules(), mike.id, -10, {}, "spent", "order-0002")).balance == 20
    with pytest.raises(tokens.NotEnoughTokens):
        await service.grant(db, Rules(), mike.id, -21, {"potion": 1}, "", "order-0003")
    assert await tokens.balance(db, mike.id) == 20
    assert await service.parcel(db, mike.id) == {}, "a refused grant gives nothing, items included"
    assert (await db.scalars(select(StoreGrant).where(StoreGrant.idempotency_key == "order-0003"))).all() == []


async def test_the_same_grant_sent_again_changes_nothing(db):
    mike = await a_player(db)
    first = await service.grant(db, Rules(), mike.id, 50, {"potion": 1}, "hi", "order-0001")
    again = await service.grant(db, Rules(), mike.id, 50, {"potion": 1}, "hi", "order-0001")
    assert (first.duplicate, again.duplicate, again.balance) == (False, True, 50)
    assert await service.parcel(db, mike.id) == {"potion": 1}


async def test_a_key_cannot_be_reused_for_another_grant(db):
    mike = await a_player(db)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    await service.grant(db, Rules(), mike.id, 50, {"potion": 1}, "hi", "order-0001")
    for other in (
        (mike.id, 51, {"potion": 1}, "hi"),
        (mike.id, 50, {"potion": 2}, "hi"),
        (mike.id, 50, {"ether": 1}, "hi"),
        (mike.id, 50, {"potion": 1}, "different"),
        (zed.id, 50, {"potion": 1}, "hi"),
    ):
        with pytest.raises(service.KeyReused):
            await service.grant(db, Rules(), *other, "order-0001")


async def test_a_purchase_and_a_grant_do_not_share_keys(db):
    mike = await a_player(db)
    await tokens.purchase(db, Rules(), mike.id, 10, "order-0001")
    assert (await service.grant(db, Rules(), mike.id, 5, {}, "", "order-0001")).duplicate is False
    assert await tokens.balance(db, mike.id) == 15


async def test_the_games_rule_has_the_final_say(db):
    mike = await a_player(db)
    rules = Says(no="Not for sale today.")
    with pytest.raises(service.Refused, match="Not for sale today."):
        await service.grant(db, rules, mike.id, 50, {"potion": 1}, "hi", "order-0001")
    assert rules.asked == [(mike.id, 50, {"potion": 1}, "hi")]
    assert await tokens.balance(db, mike.id) == 0 and await service.parcel(db, mike.id) == {}
    assert (await service.grant(db, Says(), mike.id, 50, {"potion": 1}, "hi", "order-0001")).duplicate is False, "a refusal is not remembered"


async def test_a_retry_is_not_put_to_the_rule_again(db):
    mike = await a_player(db)
    await service.grant(db, Says(), mike.id, 50, {}, "", "order-0001")
    rules = Says(no="Now closed.")
    assert (await service.grant(db, rules, mike.id, 50, {}, "", "order-0001")).duplicate is True
    assert rules.asked == []


async def test_a_credit_that_does_not_fit_the_purse_is_refused_whole(db):
    mike = await a_player(db)
    await service.grant(db, Says(purse=100), mike.id, 90, {}, "", "order-0001")
    with pytest.raises(tokens.PurseFull):
        await service.grant(db, Says(purse=100), mike.id, 11, {"potion": 1}, "", "order-0002")
    assert await tokens.balance(db, mike.id) == 90 and await service.parcel(db, mike.id) == {}
    await service.grant(db, Says(purse=100), mike.id, -50, {}, "", "order-0003")
    await service.grant(db, Says(purse=100), mike.id, 60, {}, "", "order-0004")
    assert await tokens.balance(db, mike.id) == 100


async def test_a_grant_needs_a_real_account_active_items_and_something_to_do(db):
    mike = await a_player(db)
    with pytest.raises(service.UnknownAccount):
        await service.grant(db, Rules(), 9999, 5, {}, "", "order-0001")
    for items in ({"nothing": 1}, {"retired": 1}, {"potion": 1, "nothing": 1}):
        with pytest.raises(service.UnknownItem):
            await service.grant(db, Rules(), mike.id, 5, items, "", "order-0002")
    for tokens_change, items in ((0, {}), (5, {"potion": 0}), (5, {"potion": -1})):
        with pytest.raises(service.GrantError):
            await service.grant(db, Rules(), mike.id, tokens_change, items, "", "order-0003")
    assert await tokens.balance(db, mike.id) == 0


async def test_a_parcel_cannot_overflow(db):
    mike = await a_player(db)
    await service.grant(db, Rules(), mike.id, 0, {"potion": service.MAX_PARCEL_QTY}, "", "order-0001")
    with pytest.raises(service.ParcelFull):
        await service.grant(db, Rules(), mike.id, 5, {"potion": 1}, "", "order-0002")
    assert await tokens.balance(db, mike.id) == 0


# --- the call -------------------------------------------------------------------------------------------------------------------------

def setup_player(client, username="Mike", confirmed=True):
    async def make(db):
        account = await create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=confirmed)
        if await db.scalar(select(Item).where(Item.key == "potion")) is None:
            db.add_all([an_item("potion"), an_item("ether")])
        return account.id

    return in_app_db(client, make)


def subject_of(client, account_id):
    """The opaque id the store knows the player by (made the way the sign-in provider makes it)."""
    return in_app_db(client, lambda db: oidc_service.subject_for(db, account_id, "store"))


def store(client, secret=SECRET):
    client.app.state.settings = client.app.state.settings.model_copy(update={"store_grant_secret": secret})
    return {"Authorization": f"Bearer {SECRET}"}


def test_the_call_does_not_exist_until_a_game_sets_the_secret(app_client):
    account_id = setup_player(app_client)
    reply = app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}, headers={"Authorization": f"Bearer {SECRET}"})
    assert reply.status_code == 404


def test_the_purchase_secret_does_not_open_the_store_call(app_client):
    account_id = setup_player(app_client)
    app_client.app.state.settings = app_client.app.state.settings.model_copy(update={"challenge_purchase_secret": SECRET})
    reply = app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}, headers={"Authorization": f"Bearer {SECRET}"})
    assert reply.status_code == 404


def test_the_call_grants_once_and_mails_the_player_once(app_client, mailbox):
    account_id = setup_player(app_client)
    headers = store(app_client)
    body = {"subject": subject_of(app_client, account_id), "tokens": 25, "items": {"potion": 2}, "message": "Your order #7 arrived.", "key": "order-0001"}
    assert expect(app_client.post(URL, json=body, headers=headers), 200).json() == {"balance": 25, "duplicate": False, "emailed": True}
    assert expect(app_client.post(URL, json=body, headers=headers), 200).json() == {"balance": 25, "duplicate": True, "emailed": False}
    assert in_app_db(app_client, lambda db: tokens.balance(db, account_id)) == 25
    assert in_app_db(app_client, lambda db: service.parcel(db, account_id)) == {"potion": 2}
    assert len(mailbox.sent) == 1
    mail = mailbox.sent[0]
    assert mail.to == "mike@example.com" and "Your order #7 arrived." in mail.body
    assert "+25" in mail.body and "potion x 2" in mail.body


def test_the_player_can_be_left_unmailed_or_have_no_confirmed_address(app_client, mailbox):
    quiet = setup_player(app_client)
    unconfirmed = setup_player(app_client, "Zed", confirmed=False)
    headers = store(app_client)
    one = app_client.post(URL, json={"subject": subject_of(app_client, quiet), "tokens": 5, "email": False, "key": "order-0001"}, headers=headers)
    two = app_client.post(URL, json={"subject": subject_of(app_client, unconfirmed), "tokens": 5, "key": "order-0002"}, headers=headers)
    assert expect(one, 200).json()["emailed"] is False and expect(two, 200).json()["emailed"] is False
    assert mailbox.sent == []


def test_a_mail_that_fails_does_not_undo_the_grant(app_client, mailbox, monkeypatch):
    account_id = setup_player(app_client)
    headers = store(app_client)

    async def broken(mail):
        raise OSError("the mail server is down")

    monkeypatch.setattr(app_client.app.state.mailer, "send", broken)
    reply = app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}, headers=headers)
    assert expect(reply, 200).json() == {"balance": 5, "duplicate": False, "emailed": False}


def test_the_call_needs_the_secret(app_client):
    account_id = setup_player(app_client)
    store(app_client)
    body = {"subject": subject_of(app_client, account_id), "tokens": 25, "key": "order-0001"}
    for headers in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": SECRET}, {"Authorization": f"Basic {SECRET}"}):
        reply = app_client.post(URL, json=body, headers=headers)
        assert reply.status_code == 401 and reply.headers["WWW-Authenticate"] == "Bearer"
    assert in_app_db(app_client, lambda db: tokens.balance(db, account_id)) == 0


def test_a_player_logged_in_cannot_use_the_call_without_the_secret(app_client):
    account_id = setup_player(app_client)
    store(app_client)
    app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD})
    assert app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 25, "key": "order-0001"}).status_code == 401


def test_the_call_answers_what_went_wrong(app_client):
    account_id = setup_player(app_client)
    headers = store(app_client)

    def post(**changes):
        return app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001", **changes}, headers=headers)

    assert post(subject="no-such-subject").status_code == 404
    assert post(items={"nothing": 1}).status_code == 404
    assert post(tokens=-6, key="order-0002").status_code == 409, "not enough tokens"
    assert post(key="order-0003").status_code == 200
    assert post(key="order-0003", tokens=6).status_code == 409, "the key was used for another grant"
    assert post(tokens=0, key="order-0004").status_code == 422, "nothing to do"


def test_the_games_refusal_is_shown_to_the_store(app_client):
    from terraforma.game import Game

    account_id = setup_player(app_client)
    headers = store(app_client)
    app_client.app.state.game = Game(name="Test", rules=Says(no="Not for sale today."))
    reply = app_client.post(URL, json={"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}, headers=headers)
    assert reply.status_code == 422 and reply.json()["detail"] == "Not for sale today."
    assert in_app_db(app_client, lambda db: tokens.balance(db, account_id)) == 0


def test_the_call_refuses_bad_bodies(app_client):
    account_id = setup_player(app_client)
    headers = store(app_client)
    good = {"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}
    for bad in (
        {**good, "tokens": "5"},
        {**good, "tokens": 2_000_000_000},
        {**good, "items": {"potion": 0}},
        {**good, "items": {"potion": "2"}},
        {**good, "items": [["potion", 1]]},
        {**good, "message": "x" * 1001},
        {**good, "email": "yes"},
        {**good, "key": "short"},
        {**good, "key": "k" * 59},
        {**good, "key": "has spaces in it!"},
        {**good, "extra": 1},
        {"tokens": 5, "key": "order-0001"},
        {**good, "subject": ""},
        {**good, "subject": "s" * 65},
        {"account_id": account_id, "tokens": 5, "key": "order-0001"},
    ):
        assert app_client.post(URL, json=bad, headers=headers).status_code == 422, bad


def test_the_call_is_rate_limited_by_address(app_client, monkeypatch):
    from terraforma.accounts import ratelimit

    account_id = setup_player(app_client)
    headers = store(app_client)
    monkeypatch.setattr(ratelimit, "STORE_GRANT_BY_ADDRESS", ratelimit.Limit("store-grant", 2, 15 * 60))
    body = {"subject": subject_of(app_client, account_id), "tokens": 5, "key": "order-0001"}
    assert app_client.post(URL, json=body, headers=headers).status_code == 200
    assert app_client.post(URL, json=body, headers=headers).status_code == 200
    assert app_client.post(URL, json=body, headers=headers).status_code == 429


def test_a_secret_must_be_long_or_empty():
    from pydantic import ValidationError

    from terraforma.settings import Settings

    assert Settings(session_secret="x" * 32).store_grant_secret == "", "off by default"
    assert Settings(session_secret="x" * 32, store_grant_secret=SECRET).store_grant_secret == SECRET
    with pytest.raises(ValidationError, match="store_grant_secret"):
        Settings(session_secret="x" * 32, store_grant_secret="too short")
