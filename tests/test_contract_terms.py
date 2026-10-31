"""The engine's standard contract terms: a truce judged from fights and a parcel judged from deliveries, on every database."""

from datetime import timedelta

import pytest

from terraforma import wallclock
from terraforma.contracts import service
from terraforma.contracts.models import ACTIVE, BREACHED, FULFILLED
from terraforma.contracts.terms import StandardTerms
from terraforma.delivery import service as delivery
from terraforma.economy import TeamGold
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.guild.fees import Fees
from terraforma.storage.hooks import NoStorage

from .test_trading import account, stock, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
FEES = Fees()
STORAGE = NoStorage()
TERMS = StandardTerms()
DAY = 24 * 3600


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
    mike = await account(db)
    cole_owner = await account(db, "Cole")
    a, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    b, (cole,) = await team_with(db, cole_owner, "Rearguard", ["Cole"])
    await ECONOMY.credit_team(db, a.id, 1000)
    await ECONOMY.credit_team(db, b.id, 1000)
    return a, b, aria, cole


async def contract_between(db, aria, cole, b, my_terms, their_terms, mine=100, theirs=100):
    offer = await service.offer(db, ECONOMY, FEES, TERMS, aria, "vanguard", b.id, my_terms, their_terms, mine, theirs, DAY)
    return await service.accept(db, ECONOMY, FEES, TERMS, cole, offer.id)


async def fight(db, hero_map, attacker, defender):
    """A fight that just began, with the attacker's hero in party 0 and the defender's in party 1."""
    record = FightRecord(guid=f"{attacker.id:016d}{defender.id:016d}", initial_state={}, map_id=hero_map, created_at=wallclock.now())
    db.add(record)
    await db.flush()
    db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=attacker.name, hero_id=attacker.id))
    db.add(FightParticipant(fight_id=record.id, party=1, group_index=0, character=0, name=defender.name, hero_id=defender.id))
    await db.flush()
    return record


# --- validation --------------------------------------------------------------------------------------------------------

def test_the_standard_kinds_and_their_checks_and_worth():
    assert TERMS.kinds() == {"truce", "parcel", "assassinate", "kill_boss", "escort_to"}
    assert TERMS.validate("truce", {}) is None and TERMS.validate("truce", {"value": 300}) is None
    assert "zero or more" in TERMS.validate("truce", {"value": -1})
    assert "zero or more" in TERMS.validate("truce", {"value": True})
    assert TERMS.validate("parcel", {"gold": 50}) is None
    assert TERMS.validate("parcel", {"items": [["potion", 2]]}) is None
    assert "asks for gold or items" in TERMS.validate("parcel", {})
    assert "list of [item key, quantity]" in TERMS.validate("parcel", {"items": [["potion", 0]]})
    assert "list of [item key, quantity]" in TERMS.validate("parcel", {"items": "potion"})
    assert TERMS.value("truce", {}) == 0 and TERMS.value("truce", {"value": 300}) == 300
    assert TERMS.value("parcel", {"gold": 50}) == 50 and TERMS.value("parcel", {"gold": 50, "value": 80}) == 80


# --- a truce -----------------------------------------------------------------------------------------------------------

async def test_a_truce_is_met_when_time_runs_out_with_no_attack(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract = await contract_between(db, aria, cole, b, [("truce", {})], [("truce", {})])
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract.id)).state == ACTIVE
    clock.move(DAY)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract.id)).state == FULFILLED


async def test_attacking_the_other_side_breaks_a_truce_and_loses_the_escrow(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract = await contract_between(db, aria, cole, b, [("truce", {})], [])
    contract_id = contract.id
    await fight(db, aria.map_id, aria, cole)  # a's hero attacks b's
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "a")


async def test_only_the_bound_side_breaks_its_truce_by_attacking(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract = await contract_between(db, aria, cole, b, [("truce", {})], [])  # only a promises
    contract_id = contract.id
    await fight(db, cole.map_id, cole, aria)  # b attacks a: not a's promise
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (FULFILLED, None)


async def test_a_fight_from_before_the_contract_does_not_count(clock, db):
    a, b, aria, cole = await two_sides(db)
    await fight(db, aria.map_id, aria, cole)
    clock.move(60)
    contract = await contract_between(db, aria, cole, b, [("truce", {})], [])
    clock.move(DAY)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract.id)).state == FULFILLED


# --- a parcel ----------------------------------------------------------------------------------------------------------

async def test_a_parcel_term_is_met_by_sending_what_was_asked_to_the_other_side(clock, db):
    a, b, aria, cole = await two_sides(db)
    await stock(db, aria, "potion", 3)
    contract = await contract_between(db, aria, cole, b, [("parcel", {"gold": 30, "items": [["potion", 2]]})], [])
    contract_id = contract.id
    await delivery.send(db, ECONOMY, aria, b.id, "vanguard", "contract", str(contract_id), gold=30, items=[(0, 1)])
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE  # one potion is short
    await delivery.send(db, ECONOMY, aria, b.id, "vanguard", "contract", str(contract_id), items=[(0, 1)])
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert done.state == FULFILLED


async def test_a_parcel_that_is_not_sent_in_time_breaks_the_term(clock, db):
    a, b, aria, cole = await two_sides(db)
    contract = await contract_between(db, aria, cole, b, [], [("parcel", {"gold": 30})])  # b must send a gold
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract.id)
    assert (done.state, done.breached_by) == (BREACHED, "b")


async def test_a_parcel_to_someone_else_or_with_another_reference_or_called_back_does_not_count(clock, db):
    a, b, aria, cole = await two_sides(db)
    mike = await account(db, "Dana")
    c, (dana,) = await team_with(db, mike, "Third", ["Dana"])
    contract = await contract_between(db, aria, cole, b, [("parcel", {"gold": 30})], [])
    contract_id = contract.id
    wrong_team = await delivery.send(db, ECONOMY, aria, c.id, "vanguard", "contract", str(contract_id), gold=30)
    await delivery.send(db, ECONOMY, aria, b.id, "vanguard", "contract", "other", gold=30)
    called_back = await delivery.send(db, ECONOMY, aria, b.id, "vanguard", "contract", str(contract_id), gold=30)
    await delivery.cancel(db, ECONOMY, STORAGE, called_back.id)
    assert wrong_team.to_team_id == c.id
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await delivery.send(db, ECONOMY, aria, b.id, "vanguard", "contract", str(contract_id), gold=30)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == FULFILLED


# --- an assassination: a third team, killed in fights since the contract began -----------------------------------------

def kill(team_scope="team", target=0, hero=0):
    return ("assassinate", {"target_team_id": target, "scope": team_scope, **({"hero_id": hero} if hero else {}), "value": 200})


async def three_sides(db):
    """Side a (Aria) hires side b (Cole) to kill a third team, the Targets (Dax and Dee), who are not asked."""
    a, b, aria, cole = await two_sides(db)
    dan = await account(db, "Dan")
    targets, (dax, dee) = await team_with(db, dan, "Targets", ["Dax", "Dee"])
    return a, b, targets, aria, cole, dax, dee


async def hunt(db, hunter, prey, fallen=(), tag="x"):
    """A fight that just began between the hunter's hero (party 0) and the prey's heroes (party 1); `fallen` are the prey's indexes that died."""
    record = FightRecord(guid=f"{tag}{hunter.id:015d}{prey[0].id:016d}", initial_state={}, map_id=hunter.map_id, created_at=wallclock.now())
    db.add(record)
    await db.flush()
    db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=hunter.name, hero_id=hunter.id))
    for index, hero in enumerate(prey):
        db.add(FightParticipant(fight_id=record.id, party=1, group_index=0, character=index, name=hero.name, hero_id=hero.id))
    db.add(FightActionRecord(
        fight_id=record.id, sequence=1, commands=[], events=[["Died", [1, 0, index, 10, 0]] for index in fallen], previous_hash="0", hash="1",
    ))
    await db.flush()
    return record


async def hired(db, a, b, targets, aria, cole, scope="team", hero=0, offering=False):
    """The contract where b is hired to kill the targets (or, with `offering`, a is)."""
    terms = [kill(scope, targets.id, hero)]
    offer = await service.offer(
        db, ECONOMY, FEES, TERMS, aria, "vanguard", b.id, terms if offering else [], [] if offering else terms, 200, 100, DAY, payment_gold=300,
    )
    return await service.accept(db, ECONOMY, FEES, TERMS, cole, offer.id)


def test_an_assassination_names_its_target_and_its_scope():
    assert TERMS.validate("assassinate", {"target_team_id": 3, "scope": "team"}) is None
    assert TERMS.validate("assassinate", {"target_team_id": 3, "scope": "hero", "hero_id": 9}) is None
    assert "names a target team" in TERMS.validate("assassinate", {"scope": "team"})
    assert "names a target team" in TERMS.validate("assassinate", {"target_team_id": True, "scope": "team"})
    assert "whole team or one hero" in TERMS.validate("assassinate", {"target_team_id": 3, "scope": "all"})
    assert "names the hero" in TERMS.validate("assassinate", {"target_team_id": 3, "scope": "hero"})
    assert TERMS.value("assassinate", {"target_team_id": 3, "scope": "team", "value": 200}) == 200


async def test_the_target_need_not_agree_but_must_exist_and_may_not_be_a_side_of_the_contract(clock, db):
    a, b, targets, aria, cole, dax, _dee = await three_sides(db)
    for params, match in (
        (kill("team", a.id), "kill itself or the team that hired it"),
        (kill("team", b.id), "kill itself or the team that hired it"),
        (kill("team", 99999), "no such team"),
        (kill("hero", targets.id, aria.id), "not on the target team"),
    ):
        with pytest.raises(service.ContractError, match=match):
            await service.offer(db, ECONOMY, FEES, TERMS, aria, "vanguard", b.id, [], [params], 200, 100, DAY)
    made = await service.offer(db, ECONOMY, FEES, TERMS, aria, "vanguard", b.id, [], [kill("hero", targets.id, dax.id)], 200, 100, DAY)
    assert made.state == "offered"  # the Targets were never asked


async def test_killing_the_whole_team_pays_the_hired_side_and_one_hero_is_not_enough(clock, db):
    a, b, targets, aria, cole, dax, dee = await three_sides(db)
    contract = await hired(db, a, b, targets, aria, cole)
    contract_id = contract.id
    await hunt(db, cole, [dax, dee], fallen=[0], tag="a")  # Dax falls; Dee does not
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await hunt(db, cole, [dax, dee], fallen=[1], tag="b")  # a later fight: Dee falls
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert done.state == FULFILLED and done.payment_gold == 300
    await db.refresh(b, ["gold"])
    assert b.gold == 990 + 300


async def test_one_named_hero_is_enough_when_the_contract_names_one(clock, db):
    a, b, targets, aria, cole, dax, dee = await three_sides(db)
    contract = await hired(db, a, b, targets, aria, cole, scope="hero", hero=dee.id)
    contract_id = contract.id
    await hunt(db, cole, [dax, dee], fallen=[0], tag="a")  # only Dax: the wrong hero
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await hunt(db, cole, [dax, dee], fallen=[1], tag="b")
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == FULFILLED


async def test_a_death_the_hired_side_did_not_fight_for_does_not_count_and_time_runs_out_as_a_breach(clock, db):
    a, b, targets, aria, cole, dax, dee = await three_sides(db)
    contract = await hired(db, a, b, targets, aria, cole)
    contract_id = contract.id
    await hunt(db, aria, [dax, dee], fallen=[0, 1], tag="c")  # the offering side (not the hired one) kills them
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
    await db.refresh(a, ["gold"])


async def test_a_death_before_the_contract_began_does_not_count(clock, db):
    a, b, targets, aria, cole, dax, dee = await three_sides(db)
    await hunt(db, cole, [dax, dee], fallen=[0, 1], tag="d")  # before the offer
    clock.move(10)
    contract = await hired(db, a, b, targets, aria, cole)
    contract_id = contract.id
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
