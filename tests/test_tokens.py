"""Tokens: the admin flag, balances and the ledger, and what a fight pays (an admin only as a player), on every database."""

import pytest
from sqlalchemy import select

from terraforma.accounts.service import create_account
from terraforma.fights import store
from terraforma.fights.rules import Rules
from terraforma.models import Account
from terraforma.tokens import service as tokens
from terraforma.tokens.models import TokenEntry

from .test_fight_store import PASSWORD, a_team_fights_a_rat, play_to_the_end

pytestmark = pytest.mark.anyio


class Pays(Rules):
    """A game that pays 5 tokens to every fighter, hero or monster, when a fight ends."""

    def tokens_earned(self, fight, address):
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
        def tokens_earned(self, fight, address):
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
