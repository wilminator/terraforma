"""A realm's strength over time: it drifts toward what the game says by the world clock, and settlements fall or are founded as the game says."""

import pytest
from sqlalchemy import select

from terraforma import housekeeping
from terraforma.content.loader import load_content
from terraforma.game import Game
from terraforma.models import advance_clock
from terraforma.realms import strength, supply
from terraforma.realms.hooks import Realms
from terraforma.realms.models import DORMANT, LIVE, RUINS, Realm, Settlement

from .test_realm_supply import with_supply

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


class Following(Realms):
    """Strength heads for the average fullness of the realm's supplies, 2 a tick; a hamlet falls below 10, a village is founded from 60."""

    step = 2

    def target(self, realm, fullness):
        return sum(fullness) // len(fullness) if fullness else None

    def falls(self, realm, settlement):
        return realm.strength < 10 and settlement.tier == 0

    def founds(self, realm, settlement):
        return realm.strength >= 60


async def a_realm(db, **town):
    """The supply test's realm: 'capital' (tier 3) keeps iron (10 of 50, +4 -1 a tick) and hide (20 of 20, steady); strength 7."""
    data = with_supply()
    data["settlements"][0].update(town)
    await load_content(db, data)
    return await db.scalar(select(Realm).where(Realm.key == "ardent")), await db.scalar(select(Settlement).where(Settlement.key == "capital"))


async def test_the_default_hooks_leave_strength_and_towns_alone(db):
    realm, town = await a_realm(db)
    await advance_clock(db, realm.world_id, 500)
    await strength.drift(db, Realms(), realm)
    assert realm.strength == 7
    assert await strength.judge(db, Realms()) == 0 and town.state == LIVE


async def test_strength_moves_toward_the_target_by_the_step_a_tick_and_stops_there(db):
    realm, town = await a_realm(db)
    assert sorted(await strength.fullness(db, realm)) == [20, 100], "iron 10 of 50, hide 20 of 20"
    await advance_clock(db, realm.world_id, 3)  # iron is 19 of 50 = 38%; target (38 + 100) // 2 = 69
    await strength.drift(db, Following(), realm)
    assert realm.strength == 7 + 2 * 3
    await strength.drift(db, Following(), realm)
    assert realm.strength == 13, "looking again at the same tick moves nothing"
    await advance_clock(db, realm.world_id, 100)  # full: target 100
    await strength.drift(db, Following(), realm)
    assert realm.strength == 100, "never past the target"
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, realm.world_id, 1000)  # iron is empty: target (0 + 100) // 2 = 50
    await strength.drift(db, Following(), realm)
    assert realm.strength == 50, "and down as well as up"


async def test_a_realm_with_nothing_live_has_no_target_and_stays(db):
    realm, town = await a_realm(db, state="dormant")
    await advance_clock(db, realm.world_id, 10)
    assert await strength.fullness(db, realm) == []
    await strength.drift(db, Following(), realm)
    assert realm.strength == 7


async def test_a_new_realm_starts_counting_at_the_clock_it_was_made(db):
    from terraforma.world.start import ensure_start

    world_id = (await ensure_start(db)).world_id
    await advance_clock(db, world_id, 50)
    realm, _town = await a_realm(db)
    assert realm.as_of_tick == 50
    await strength.drift(db, Following(), realm)
    assert realm.strength == 7, "no time had passed for it"


async def test_judge_lets_a_failing_realms_hamlet_fall_and_a_strong_realm_found_one(db):
    realm, capital = await a_realm(db)
    hamlet = await db.scalar(select(Settlement).where(Settlement.key == "hamlet"))
    realm.strength = 5
    capital.tier = 0
    await db.flush()
    assert await strength.judge(db, Following()) == 1
    assert capital.state == RUINS and hamlet.state == DORMANT, "5 is too weak to found anything"
    realm.strength = 80
    assert await strength.judge(db, Following()) == 1
    assert hamlet.state == LIVE, "dormant before ruins"
    assert capital.state == RUINS
    assert await strength.judge(db, Following()) == 1, "next pass the ruins are raised"
    assert capital.state == LIVE


async def test_only_one_settlement_is_founded_a_pass_and_one_that_cannot_be_is_skipped(db, monkeypatch):
    realm, capital = await a_realm(db)
    hamlet = await db.scalar(select(Settlement).where(Settlement.key == "hamlet"))
    realm.strength = 80
    capital.state, hamlet.state = RUINS, DORMANT
    await db.flush()

    class Cannot(Following):
        def founds(self, realm, settlement):
            return True

    original = strength.service.found

    async def refuse_hamlet(session, settlement):
        if settlement.key == "hamlet":
            raise strength.service.RealmError("no room")
        return await original(session, settlement)

    monkeypatch.setattr(strength.service, "found", refuse_hamlet)
    assert await strength.judge(db, Cannot()) == 1
    assert hamlet.state == DORMANT and capital.state == LIVE


async def test_the_housekeeping_job_runs_the_games_realms_and_does_nothing_without_a_game(db):
    realm, capital = await a_realm(db)
    realm.strength = 5
    capital.tier = 0
    await db.flush()
    assert await housekeeping.realm_strength(db, 0, None) == 0, "no game, no rules"
    db.info["game"] = Game(name="Test", realms=Following())
    assert await housekeeping.realm_strength(db, 0, None) == 1
    assert capital.state == RUINS


async def test_a_founding_is_skipped_when_its_map_has_no_room_and_the_next_one_goes_ahead(db):
    from terraforma.models import Map
    from terraforma.realms import service
    from terraforma.world.start import ensure_start

    realm, capital = await a_realm(db)
    world_id = (await ensure_start(db)).world_id
    crowded = Map(world_id=world_id, name="crowded", title="Crowded")
    db.add(crowded)
    await db.flush()
    for key in ("one", "two"):
        await service.add_settlement(db, realm, crowded.id, key, key)
    full = await service.add_settlement(db, realm, crowded.id, "three", "Three", state=DORMANT)
    hamlet = await db.scalar(select(Settlement).where(Settlement.key == "hamlet"))  # dormant, on the hub
    realm.strength = 80
    await db.flush()
    assert [town.key for town in await service.settlements(db, realm, DORMANT)] == ["hamlet", "three"]
    assert await strength.judge(db, Following()) == 1
    assert hamlet.state == LIVE and full.state == DORMANT
    assert await strength.judge(db, Following()) == 0, "'three' has nowhere to go, so nothing else changes"
