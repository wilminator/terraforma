"""A contract that hires a party to kill a boss inside another party's run of a quest: getting in, and being paid when the boss falls."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.contracts import service
from terraforma.contracts.models import ACTIVE, BREACHED, FULFILLED
from terraforma.contracts.terms import StandardTerms
from terraforma.economy import TeamGold
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.guild.fees import Fees
from terraforma.heroes.models import Hero, Team, TeamMember
from terraforma.instances import service as instances
from terraforma.models import Map
from terraforma.parties import service as parties
from terraforma.storage.hooks import NoStorage
from terraforma.world.start import ensure_start

from .test_instances import RULES
from .test_pvp import two_parties

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
FEES = Fees()
STORAGE = NoStorage()
TERMS = StandardTerms()
DAY = 24 * 3600
BOSS = [("kill_boss", {"monster": "warden", "value": 200})]


@pytest.fixture
def clock(monkeypatch):
    now = [wallclock.now()]
    monkeypatch.setattr(wallclock, "now", lambda: now[0])

    class Clock:
        @staticmethod
        def move(seconds):
            now[0] += timedelta(seconds=seconds)

    return Clock


async def hero_named(db, name):
    return await db.scalar(select(Hero).where(Hero.name == name))


async def crypt(db, name="crypt"):
    hub = await ensure_start(db)
    found = Map(world_id=hub.world_id, name=name, width=10, height=10)
    db.add(found)
    await db.flush()
    return found


async def setup(db, host_in_instance=True):
    """Mike's Vanguard (Aria) is on a quest in a private instance of the crypt and hires Zed's Scouts (Cleo) to kill its warden."""
    _mike, _zed, team, party, foes = await two_parties(db)
    aria, cleo = await hero_named(db, "Aria"), await hero_named(db, "Cleo")
    scouts_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == cleo.id))
    found = await crypt(db)
    if host_in_instance:
        await instances.enter(db, RULES, party.id, "warden-quest", instances.PRIVATE, found, 3, 4)
    await ECONOMY.credit_team(db, team.id, 1000)
    await ECONOMY.credit_team(db, scouts_id, 1000)
    offer = await service.offer(db, ECONOMY, FEES, TERMS, aria, "vanguard", scouts_id, [], BOSS, 200, 100, DAY, payment_gold=300)
    contract = await service.accept(db, ECONOMY, FEES, TERMS, cleo, offer.id)
    return team, scouts_id, aria, cleo, party, foes, found, contract


async def boss_fight(db, hero, map_id, fallen=True, tag="x"):
    record = FightRecord(guid=f"{tag}{hero.id:031d}", initial_state={}, map_id=map_id, created_at=wallclock.now(), finished=True)
    db.add(record)
    await db.flush()
    db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=hero.name, hero_id=hero.id))
    db.add(FightParticipant(fight_id=record.id, party=1, group_index=0, character=0, name="Warden", monster_key="warden"))
    db.add(FightActionRecord(
        fight_id=record.id, sequence=1, commands=[], events=[["Died", [1, 0, 0, 10, 0]]] if fallen else [], previous_hash="0", hash="1",
    ))
    await db.flush()


def test_a_boss_kill_names_its_boss():
    assert TERMS.validate("kill_boss", {"monster": "warden"}) is None
    assert "names the boss" in TERMS.validate("kill_boss", {})
    assert "names the boss" in TERMS.validate("kill_boss", {"monster": ""})
    assert TERMS.value("kill_boss", {"monster": "warden", "value": 200}) == 200


async def test_the_hired_party_is_let_into_the_other_sides_instance_and_stands_beside_them(clock, db):
    _team, _scouts, _aria, cleo, party, foes, found, contract = await setup(db)
    contract_id = contract.id
    await service.enter_instance(db, cleo, contract_id)
    there = await instances.instance_of(db, foes.id)
    assert there is not None and there.id == (await instances.instance_of(db, party.id)).id
    assert (foes.map_id, foes.x, foes.y) == (found.id, party.x, party.y)
    assert (await service.terms_of(db, contract))[0].params["entered_map_id"] == found.id
    with pytest.raises(service.ContractError, match="already in an instance"):
        await service.enter_instance(db, cleo, contract_id)


async def test_only_the_hired_side_can_go_in_and_only_when_the_host_is_in_an_instance(clock, db):
    _team, _scouts, aria, cleo, _party, _foes, _found, contract = await setup(db, host_in_instance=False)
    contract_id = contract.id
    with pytest.raises(service.ContractError, match="hires you to enter no instance"):
        await service.enter_instance(db, aria, contract_id)  # the host is not the one hired
    with pytest.raises(service.ContractError, match="not in an instance"):
        await service.enter_instance(db, cleo, contract_id)


async def test_killing_the_boss_inside_the_instance_pays_the_hired_side(clock, db):
    team, scouts_id, _aria, cleo, _party, _foes, found, contract = await setup(db)
    contract_id = contract.id
    await service.enter_instance(db, cleo, contract_id)
    await boss_fight(db, cleo, found.id, fallen=False, tag="a")  # the boss stands
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await boss_fight(db, cleo, found.id, tag="b")
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert done.state == FULFILLED and done.payment_gold == 300
    scouts = await db.get(Team, scouts_id)
    await db.refresh(scouts, ["gold"])
    assert scouts.gold == 1000 - 10 + 300  # fee gone, escrow back, payment in


async def test_a_boss_killed_anywhere_else_or_without_going_in_does_not_count(clock, db):
    _team, _scouts, _aria, cleo, _party, _foes, found, contract = await setup(db)
    contract_id = contract.id
    elsewhere = await crypt(db, "other")
    await boss_fight(db, cleo, elsewhere.id, tag="c")  # before going in: no entry is recorded
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await service.enter_instance(db, cleo, contract_id)
    await boss_fight(db, cleo, elsewhere.id, tag="d")  # the wrong map
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    clock.move(DAY)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
