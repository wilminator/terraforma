"""Contracts: offers, acceptance, breach escrow and its payout, on every database."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.contracts import service
from terraforma.contracts.hooks import Contracts
from terraforma.contracts.models import (
    ACTIVE, BREACHED, BROKEN, DECLINED, EXPIRED, FULFILLED, MET, OFFERED, PENDING, WITHDRAWN, Contract, ContractTerm,
)
from terraforma.economy import TeamGold
from terraforma.escrow.models import FORFEITED, HELD, REFUNDED, Escrow
from terraforma.guild.fees import Fees, RankFees
from terraforma.guild import ranks
from terraforma.storage.hooks import NoStorage

from .test_trading import account, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
FEES = Fees()
STORAGE = NoStorage()
HOUR = 3600
DAY = 24 * HOUR


class Pacts(Contracts):
    """Two kinds of term whose verdicts a test sets: `verdicts[(contract id, kind)]`."""

    def __init__(self):
        self.verdicts = {}

    def kinds(self):
        return {"truce", "escort"}

    def validate(self, kind, params):
        return "an escort names a destination" if kind == "escort" and "to" not in params else None

    def value(self, kind, params):
        return 400 if kind == "truce" else 100

    async def check(self, session, contract, term, ended):
        return self.verdicts.get((contract.id, term.kind), MET if ended else PENDING)


PACTS = Pacts()


@pytest.fixture
def clock(monkeypatch):
    """The wall clock, moved by `clock.move(seconds)`."""
    now = [wallclock.now()]
    monkeypatch.setattr(wallclock, "now", lambda: now[0])

    class Clock:
        @staticmethod
        def move(seconds):
            now[0] += timedelta(seconds=seconds)

    return Clock


async def two_sides(db):
    """Side a (Aria on Vanguard, 1000 gold) and side b (Cole on Rearguard, 1000 gold), owned by two players."""
    mike = await account(db)
    cole_owner = await account(db, "Cole")
    a, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    b, (cole,) = await team_with(db, cole_owner, "Rearguard", ["Cole"])
    await ECONOMY.credit_team(db, a.id, 1000)
    await ECONOMY.credit_team(db, b.id, 1000)
    return a, b, aria, cole


async def gold_of(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


TRUCE = [("truce", {})]
ESCORT = [("escort", {"to": "Eastgate"})]


async def offered(db, aria, b, my_terms=TRUCE, their_terms=ESCORT, mine=250, theirs=325, duration=DAY, guild="vanguard", contracts=PACTS, fees=FEES, payment_gold=0):
    return await service.offer(db, ECONOMY, fees, contracts, aria, guild, b.id, my_terms, their_terms, mine, theirs, duration, payment_gold)


# --- offering ----------------------------------------------------------------------------------------------------------

async def test_an_offer_takes_the_offering_sides_fee_and_escrow_and_lists_both_sides_terms(clock, db):
    a, b, aria, _cole = await two_sides(db)
    contract = await offered(db, aria, b)
    assert (contract.state, contract.a_team_id, contract.b_team_id, contract.a_escrow_gold, contract.b_escrow_gold) == (OFFERED, a.id, b.id, 250, 325)
    assert contract.offer_expires_at == wallclock.timestamp() + 3 * DAY
    assert await gold_of(db, a) == 1000 - 10 - 250 and await gold_of(db, b) == 1000
    assert [(t.side, t.kind, t.state) for t in await service.terms_of(db, contract)] == [("a", "truce", PENDING), ("b", "escort", PENDING)]
    deposit = await db.get(Escrow, contract.a_escrow_id)
    assert (deposit.state, deposit.gold, deposit.purpose, deposit.ref) == (HELD, 250, "contract", f"{contract.id}:a")
    assert contract.b_escrow_id is None  # theirs is put down when they accept


async def test_each_sides_escrow_must_reach_the_minimum_set_by_the_others_stake(clock, db):
    _a, b, aria, _cole = await two_sides(db)
    # a is bound by a truce (400) and b by an escort (100); the minimum is half of what the other side has at stake
    # b's escrow 325 -> a must put down at least (100 + 325) / 2 = 213; a's escrow 250 -> b must put down at least (400 + 250) / 2 = 325
    with pytest.raises(service.ContractError, match="other side's escrow is at least 325"):
        await offered(db, aria, b, mine=250, theirs=324)
    with pytest.raises(service.ContractError, match="offering side's escrow is at least 213"):
        await offered(db, aria, b, mine=212, theirs=325)
    ok = await offered(db, aria, b, mine=250, theirs=325)
    assert ok.state == OFFERED
    assert service.minimum_escrow(PACTS, "vanguard", 400, 250) == 325


async def test_the_minimum_percent_is_the_games_to_set(clock, db):
    _a, b, aria, _cole = await two_sides(db)

    class Strict(Pacts):
        def minimum_percent(self, guild):
            return 90

    with pytest.raises(service.ContractError, match="at least"):
        await offered(db, aria, b, contracts=Strict(), mine=250, theirs=325)


async def test_an_offer_that_cannot_be_made_changes_nothing(clock, db):
    a, b, aria, cole = await two_sides(db)
    cases = (
        (dict(my_terms=[("nope", {})]), "no kind of term"),
        (dict(their_terms=[("escort", {})]), "names a destination"),
        (dict(my_terms=[], their_terms=[]), "binds someone"),
        (dict(duration=0), "from a second to a year"),
        (dict(mine=-1), "zero or more"),
    )
    for changes, match in cases:
        with pytest.raises(service.ContractError, match=match):
            await offered(db, aria, b, **changes)
    with pytest.raises(service.ContractError, match="with itself"):
        await service.offer(db, ECONOMY, FEES, PACTS, aria, "vanguard", a.id, TRUCE, ESCORT, 250, 325, DAY)
    with pytest.raises(service.ContractError, match="no such team"):
        await service.offer(db, ECONOMY, FEES, PACTS, aria, "vanguard", 9999, TRUCE, ESCORT, 250, 325, DAY)
    await ECONOMY.debit(db, aria, 900)  # now only 100 gold is left: not enough for the fee and the escrow
    with pytest.raises(service.ContractError, match="not enough gold"):
        await offered(db, aria, b)
    assert await db.scalar(select(func.count()).select_from(Contract)) == 0
    assert await db.scalar(select(func.count()).select_from(Escrow)) == 0
    assert await gold_of(db, a) == 100


async def test_each_side_pays_the_fee_at_its_own_rank(clock, db):
    a, b, aria, cole = await two_sides(db)
    await ranks.set_rank(db, a.id, "vanguard", 1)
    rule = RankFees(rank_discounts=(0, 50))
    contract = await offered(db, aria, b, fees=rule)
    assert await gold_of(db, a) == 1000 - 5 - 250  # half off for a
    await service.accept(db, ECONOMY, rule, PACTS, cole, contract.id)
    assert await gold_of(db, b) == 1000 - 10 - 325  # no standing for b: the full fee


# --- answering ---------------------------------------------------------------------------------------------------------

async def test_accepting_puts_the_other_escrow_down_and_starts_the_contract(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract = await offered(db, aria, b)
    started = await service.accept(db, ECONOMY, FEES, PACTS, cole, contract.id)
    assert (started.state, started.starts_at, started.ends_at) == (ACTIVE, wallclock.timestamp(), wallclock.timestamp() + DAY)
    assert await gold_of(db, b) == 1000 - 10 - 325
    deposit = await db.get(Escrow, started.b_escrow_id)
    assert (deposit.team_id, deposit.gold, deposit.ref) == (b.id, 325, f"{started.id}:b")


async def test_only_the_asked_team_can_answer_and_only_while_the_offer_stands(clock, db):
    _a, b, aria, cole = await two_sides(db)
    contract = await offered(db, aria, b)
    contract_id = contract.id
    with pytest.raises(service.ContractError, match="not for your team"):
        await service.accept(db, ECONOMY, FEES, PACTS, aria, contract_id)
    with pytest.raises(service.ContractError, match="not for your team"):
        await service.decline(db, ECONOMY, STORAGE, aria, contract_id)
    clock.move(3 * DAY)
    with pytest.raises(service.ContractError, match="gone"):
        await service.accept(db, ECONOMY, FEES, PACTS, cole, contract_id)
    assert await gold_of(db, b) == 1000


async def test_an_acceptance_that_cannot_be_paid_leaves_the_offer_standing(clock, db):
    _a, b, aria, cole = await two_sides(db)
    contract = await offered(db, aria, b)
    contract_id = contract.id
    await ECONOMY.debit(db, cole, 900)
    with pytest.raises(service.ContractError, match="not enough gold"):
        await service.accept(db, ECONOMY, FEES, PACTS, cole, contract_id)
    await db.refresh(contract)
    assert contract.state == OFFERED and contract.b_escrow_id is None and await gold_of(db, b) == 100


async def test_declining_or_withdrawing_returns_the_escrow_but_not_the_fee(clock, db):
    a, b, aria, cole = await two_sides(db)
    first = await offered(db, aria, b)
    declined = await service.decline(db, ECONOMY, STORAGE, cole, first.id)
    assert declined.state == DECLINED and await gold_of(db, a) == 990
    second = await offered(db, aria, b)
    second_id = second.id
    with pytest.raises(service.ContractError, match="no such offer of yours"):
        await service.withdraw(db, ECONOMY, STORAGE, cole, second_id)
    withdrawn = await service.withdraw(db, ECONOMY, STORAGE, aria, second_id)
    assert withdrawn.state == WITHDRAWN and await gold_of(db, a) == 980
    with pytest.raises(service.ContractError, match="settled already"):
        await service.withdraw(db, ECONOMY, STORAGE, aria, second_id)
    assert (await db.get(Escrow, declined.a_escrow_id)).state == REFUNDED


async def test_an_unanswered_offer_expires_and_returns_the_escrow(clock, db):
    a, b, aria, _cole = await two_sides(db)
    contract = await offered(db, aria, b)
    assert await service.expire_offers(db, ECONOMY, STORAGE) == 0
    clock.move(3 * DAY)
    assert await service.expire_offers(db, ECONOMY, STORAGE) == 1
    await db.refresh(contract)
    assert contract.state == EXPIRED and await gold_of(db, a) == 990
    assert await service.expire_offers(db, ECONOMY, STORAGE) == 0


# --- judging -----------------------------------------------------------------------------------------------------------

async def active(db, aria, b, cole, **what):
    pacts = Pacts()
    contract = await offered(db, aria, b, contracts=pacts, **what)
    await service.accept(db, ECONOMY, FEES, pacts, cole, contract.id)
    return contract, pacts


async def test_a_contract_kept_to_the_end_returns_both_escrows(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole)
    contract_id = contract.id
    assert (await service.judge(db, ECONOMY, STORAGE, pacts, contract_id)).state == ACTIVE  # nothing decided yet
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract_id)
    assert (done.state, done.breached_by) == (FULFILLED, None)
    assert await gold_of(db, a) == 990 and await gold_of(db, b) == 990  # only the fees are gone
    assert [t.state for t in await service.terms_of(db, done)] == [MET, MET]
    assert (await db.get(Escrow, done.a_escrow_id)).state == REFUNDED and (await db.get(Escrow, done.b_escrow_id)).state == REFUNDED


async def test_a_contract_whose_every_term_is_met_early_ends_early(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole)
    pacts.verdicts = {(contract.id, "truce"): MET, (contract.id, "escort"): MET}
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert done.state == FULFILLED and await gold_of(db, a) == 990


async def test_a_breach_gives_the_breakers_escrow_to_the_other_side(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole)
    contract_id = contract.id
    pacts.verdicts = {(contract_id, "truce"): BROKEN}  # side a broke the truce
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "a")
    assert await gold_of(db, a) == 990 - 250 and await gold_of(db, b) == 990 + 250  # a lost its escrow to b; b got its own back
    assert (await db.get(Escrow, done.a_escrow_id)).state == FORFEITED and (await db.get(Escrow, done.b_escrow_id)).state == REFUNDED
    again = await service.judge(db, ECONOMY, STORAGE, pacts, contract_id)
    assert again.state == BREACHED and await gold_of(db, b) == 990 + 250  # settled once


async def test_a_term_still_pending_when_time_runs_out_counts_as_broken(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole)
    pacts.verdicts = {(contract.id, "escort"): PENDING}  # b never got there
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
    assert await gold_of(db, a) == 990 + 325 and await gold_of(db, b) == 990 - 325


async def test_when_both_sides_break_a_term_each_escrow_goes_to_the_other(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole)
    pacts.verdicts = {(contract.id, "truce"): BROKEN, (contract.id, "escort"): BROKEN}
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "ab")
    assert await gold_of(db, a) == 990 - 250 + 325 and await gold_of(db, b) == 990 - 325 + 250


async def test_judging_an_offer_or_a_settled_contract_does_nothing(clock, db):
    a, b, aria, cole = await two_sides(db)
    offer = await offered(db, aria, b)
    assert (await service.judge(db, ECONOMY, STORAGE, PACTS, offer.id)).state == OFFERED
    with pytest.raises(service.ContractError, match="no such contract"):
        await service.judge(db, ECONOMY, STORAGE, PACTS, 9999)


# --- the payment: held from the offer, paid when the other side keeps its terms -----------------------------------------

async def test_a_payment_is_held_from_the_offer_and_paid_when_the_other_side_keeps_its_terms(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole, payment_gold=300)
    assert await gold_of(db, a) == 1000 - 10 - 250 - 300  # the payment left a's purse with the offer
    deposit = await db.get(Escrow, contract.payment_escrow_id)
    assert (deposit.state, deposit.gold, deposit.ref) == (HELD, 300, f"{contract.id}:p")
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert done.state == FULFILLED and done.payment_gold == 300
    assert await gold_of(db, a) == 990 - 300 and await gold_of(db, b) == 990 + 300
    assert (await db.get(Escrow, done.payment_escrow_id)).state == FORFEITED  # handed to b


async def test_a_payment_goes_back_when_the_paid_side_breaks_a_term(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole, payment_gold=300)
    pacts.verdicts = {(contract.id, "escort"): BROKEN}
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
    assert await gold_of(db, a) == 990 + 325 and await gold_of(db, b) == 990 - 325  # b's escrow went to a; the payment came back
    assert (await db.get(Escrow, done.payment_escrow_id)).state == REFUNDED


async def test_a_payment_is_still_owed_when_only_the_offering_side_broke_a_term(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract, pacts = await active(db, aria, b, cole, payment_gold=300)
    pacts.verdicts = {(contract.id, "truce"): BROKEN, (contract.id, "escort"): MET}
    done = await service.judge(db, ECONOMY, STORAGE, pacts, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "a")
    assert await gold_of(db, b) == 990 + 250 + 300 and await gold_of(db, a) == 990 - 250 - 300


async def test_a_payment_comes_back_when_the_offer_is_declined_withdrawn_or_expires(clock, db):
    a, b, aria, cole = await two_sides(db)
    declined = await offered(db, aria, b, payment_gold=300)
    assert await gold_of(db, a) == 1000 - 10 - 250 - 300
    await service.decline(db, ECONOMY, STORAGE, cole, declined.id)
    assert await gold_of(db, a) == 990
    withdrawn = await offered(db, aria, b, payment_gold=300)
    await service.withdraw(db, ECONOMY, STORAGE, aria, withdrawn.id)
    assert await gold_of(db, a) == 980
    stale = await offered(db, aria, b, payment_gold=300)
    clock.move(4 * DAY)
    assert await service.expire_offers(db, ECONOMY, STORAGE) == 1
    assert await gold_of(db, a) == 970 and (await db.get(Escrow, stale.payment_escrow_id)).state == REFUNDED


async def test_a_payment_the_offering_side_cannot_afford_changes_nothing(clock, db):
    a, b, aria, _cole = await two_sides(db)
    with pytest.raises(service.ContractError):
        await offered(db, aria, b, payment_gold=900)
    assert await gold_of(db, a) == 1000
    assert await db.scalar(select(func.count()).select_from(Contract)) == 0
