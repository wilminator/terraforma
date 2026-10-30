"""An escort contract: the escorted team joins the escort's party, neither can leave it, and the party is judged by where it ends up."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.contracts import escort, service
from terraforma.contracts.models import ACTIVE, BREACHED, FULFILLED
from terraforma.contracts.terms import StandardTerms
from terraforma.economy import TeamGold
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.guild.fees import Fees
from terraforma.heroes.models import Hero, Team, TeamMember
from terraforma.instances import service as instances
from terraforma.maps import travel
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


@pytest.fixture
def clock(monkeypatch):
    now = [wallclock.now()]
    monkeypatch.setattr(wallclock, "now", lambda: now[0])

    class Clock:
        @staticmethod
        def move(seconds):
            now[0] += timedelta(seconds=seconds)

    return Clock


def going(to_map="pass", scope="team", **more):
    return [("escort_to", {"to_map": to_map, "scope": scope, "value": 200, **more})]


async def place(db, name, size=10):
    hub = await ensure_start(db)
    found = Map(world_id=hub.world_id, name=name, width=size, height=size)
    db.add(found)
    await db.flush()
    return found


async def sides(db):
    """Zed's Scouts (Cleo) want to be taken somewhere by Mike's Vanguard (Aria), whose party they will join."""
    _mike, _zed, vanguard, party, foes = await two_parties(db)
    aria = await db.scalar(select(Hero).where(Hero.name == "Aria"))
    cleo = await db.scalar(select(Hero).where(Hero.name == "Cleo"))
    scouts_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == cleo.id))
    await parties.leave_party(db, scouts_id)  # the escorted team has no party of its own to bring
    await ECONOMY.credit_team(db, vanguard.id, 1000)
    await ECONOMY.credit_team(db, scouts_id, 1000)
    return vanguard, scouts_id, aria, cleo, party


async def hired(db, terms, party_size=20, payment=300, mine=200, theirs=100):
    """Scouts (a) offer, Vanguard (b) accept: Vanguard is bound by `terms`, so it is the escort."""
    vanguard, scouts_id, aria, cleo, party = await sides(db)
    offer = await service.offer(db, ECONOMY, FEES, TERMS, cleo, "vanguard", vanguard.id, [], terms, mine, theirs, DAY, payment_gold=payment)
    contract = await service.accept(db, ECONOMY, FEES, TERMS, aria, offer.id, party_size)
    return vanguard, scouts_id, aria, cleo, party, contract


def test_an_escort_names_a_place_and_who_must_stay_alive():  # (no database)
    assert TERMS.validate("escort_to", {"to_map": "pass", "scope": "team"}) is None
    assert TERMS.validate("escort_to", {"to_map": "pass", "scope": "team", "x": 2, "y": 3, "dungeon": True}) is None
    assert "destination map" in TERMS.validate("escort_to", {"scope": "team"})
    assert "x and a y" in TERMS.validate("escort_to", {"to_map": "pass", "scope": "team", "x": 2})
    assert "true or false" in TERMS.validate("escort_to", {"to_map": "pass", "scope": "team", "dungeon": "yes"})
    assert "whole team alive or one hero" in TERMS.validate("escort_to", {"to_map": "pass"})
    assert "names the hero" in TERMS.validate("escort_to", {"to_map": "pass", "scope": "hero"})


async def test_an_offer_needs_a_real_destination_and_a_hero_of_the_escorted_team(clock, db):
    vanguard, scouts_id, aria, cleo, _party = await sides(db)
    await place(db, "pass")
    for terms, match in ((going("nowhere"), "no such destination"), (going(scope="hero", hero_id=aria.id), "not on the escorted team")):
        with pytest.raises(service.ContractError, match=match):
            await service.offer(db, ECONOMY, FEES, TERMS, cleo, "vanguard", vanguard.id, [], terms, 200, 100, DAY)
    made = await service.offer(db, ECONOMY, FEES, TERMS, cleo, "vanguard", vanguard.id, [], going(scope="hero", hero_id=cleo.id), 200, 100, DAY)
    assert made.state == "offered"


async def test_accepting_brings_the_escorted_team_into_the_escorts_party_and_notes_where_it_began(clock, db):
    await place(db, "pass")
    vanguard, scouts_id, _aria, _cleo, party, contract = await hired(db, going())
    assert (await parties.party_of(db, scouts_id)).id == party.id == (await parties.party_of(db, vanguard.id)).id
    term = (await service.terms_of(db, contract))[0]
    assert term.params["origin"] == [party.map_id, party.x, party.y]


async def test_an_escort_needs_room_in_a_party_its_team_owns(clock, db):
    await place(db, "pass")
    with pytest.raises(service.ContractError, match="room for 1 heroes"):
        await hired(db, going(), party_size=1)
    # (nothing changed: the offer still stands and nothing was paid)
    assert await db.scalar(select(Team.gold).where(Team.name == "Vanguard")) == 1000


async def test_neither_team_can_leave_the_party_until_the_contract_ends(clock, db):
    await place(db, "pass")
    vanguard, scouts_id, _aria, _cleo, _party, contract = await hired(db, going())
    for team in (vanguard.id, scouts_id):
        with pytest.raises(parties.PartyError, match="stays in its party"):
            await parties.leave_party(db, team)
    assert await escort.restricted(db, [vanguard.id, scouts_id]) and await escort.locked(db, scouts_id)
    clock.move(DAY)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract.id)).state == BREACHED  # never got there
    assert not await escort.locked(db, scouts_id)
    assert await parties.leave_party(db, scouts_id) is not None


async def test_arriving_on_the_map_fulfils_the_escort_and_pays_the_escort(clock, db):
    destination_id = (await place(db, "pass")).id
    vanguard, _scouts, _aria, _cleo, party, contract = await hired(db, going())
    contract_id = contract.id
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await travel.relocate(db, party, await db.get(Map, destination_id), 4, 4)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert done.state == FULFILLED
    await db.refresh(vanguard, ["gold"])
    assert vanguard.gold == 1000 - 10 + 300  # fee gone, escrow back, payment in


async def test_a_tile_destination_is_noted_when_the_party_stands_on_it(clock, db):
    destination_id = (await place(db, "pass")).id
    _vanguard, _scouts, _aria, _cleo, party, contract = await hired(db, going(x=5, y=6))
    contract_id = contract.id
    await travel.relocate(db, party, await db.get(Map, destination_id), 4, 4)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    await travel.relocate(db, party, await db.get(Map, destination_id), 5, 6)
    await travel.relocate(db, party, await db.get(Map, destination_id), 7, 7)  # (walks on: the arrival was noted)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == FULFILLED


async def test_a_dungeon_destination_needs_the_way_in_and_the_way_back(clock, db):
    dungeon_id = (await place(db, "crypt")).id
    _vanguard, _scouts, _aria, _cleo, party, contract = await hired(db, going("crypt", dungeon=True))
    contract_id = contract.id
    start = (party.map_id, party.x, party.y)
    await travel.relocate(db, party, await db.get(Map, dungeon_id), 2, 2)  # on the map, but not in the instance
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE
    back = await db.get(Map, start[0])
    await travel.relocate(db, party, back, start[1], start[2])
    await instances.enter(db, RULES, party.id, "crypt-quest", instances.PRIVATE, await db.get(Map, dungeon_id), 1, 1)
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == ACTIVE  # in, but not back
    await instances.leave(db, party.id)  # back where it began
    assert (await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)).state == FULFILLED


async def test_the_escorted_falling_fails_the_escort(clock, db):
    destination_id = (await place(db, "pass")).id
    _vanguard, _scouts, _aria, cleo, party, contract = await hired(db, going())
    contract_id = contract.id
    record = FightRecord(guid=f"f{cleo.id:031d}", initial_state={}, map_id=party.map_id, created_at=wallclock.now(), finished=True)
    db.add(record)
    await db.flush()
    db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=cleo.name, hero_id=cleo.id))
    db.add(FightActionRecord(fight_id=record.id, sequence=1, commands=[], events=[["Died", [0, 0, 0, 10, 0]]], previous_hash="0", hash="1"))
    await db.flush()
    await travel.relocate(db, party, await db.get(Map, destination_id), 4, 4)  # (it got there, but too late to matter)
    done = await service.judge(db, ECONOMY, STORAGE, TERMS, contract_id)
    assert (done.state, done.breached_by) == (BREACHED, "b")
