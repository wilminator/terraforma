"""Delivery: parcels sent to a team's guild, arriving at a wall-clock time, collected or called back, on every database."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.delivery import service as delivery
from terraforma.delivery.models import CANCELLED, COLLECTED, TRANSIT, Shipment, ShipmentItem
from terraforma.economy import TeamGold
from terraforma.heroes import inventory, service
from terraforma.storage import service as storage_service
from terraforma.storage.hooks import NoStorage, TeamStorage

from .test_trading import account, holding, stock, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

ECONOMY = TeamGold()
HOUR = 3600


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


async def two_teams(db):
    """Two teams (Aria's, with 100 gold, two potions and a sword, and Cole's)."""
    mike = await account(db)
    mine, (aria,) = await team_with(db, mike, "Vanguard", ["Aria"])
    yours, (cole,) = await team_with(db, mike, "Rearguard", ["Cole"])
    await ECONOMY.credit_team(db, mine.id, 100)
    await stock(db, aria, "potion", 2)
    await stock(db, aria, "sword")
    return mike, mine, yours, aria, cole


async def gold_of(db, team):
    await db.refresh(team, ["gold"])
    return team.gold


# --- sending -----------------------------------------------------------------------------------------------------------

async def test_a_parcel_takes_gold_and_items_from_the_sender_and_waits_in_transit(clock, db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", "item-9", delay=HOUR, gold=40, items=[(0, 2)])
    assert (parcel.guild, parcel.purpose, parcel.ref, parcel.from_team_id, parcel.to_team_id, parcel.state) == (
        "vanguard", "purchase", "item-9", mine.id, yours.id, TRANSIT)
    assert parcel.arrives_at == wallclock.timestamp() + HOUR
    assert await gold_of(db, mine) == 60 and await holding(db, aria, "potion") == 0 and await holding(db, aria, "sword") == 1
    assert await delivery.contents(db, parcel) == {"gold": 40, "items": [("potion", 2)]}


async def test_a_short_purse_or_a_bad_stack_sends_nothing_and_takes_nothing(clock, db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    for bad, why in (
        ({"gold": 101, "items": [(0, 2)]}, "not enough gold"),
        ({"gold": 10, "items": [(5, 1)]}, "nothing in that position"),
        ({"gold": 10, "items": [(0, 3)]}, "you have 2"),
        ({"gold": 10, "items": [(0, 0)]}, "at least 1"),
        ({"gold": 10, "items": [(0, 1), (0, 1)]}, "named once"),
        ({}, "nothing to send"),
        ({"gold": -1}, "zero or more"),
    ):
        with pytest.raises(delivery.DeliveryError, match=why):
            await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", **bad)
    with pytest.raises(delivery.DeliveryError, match="delay"):
        await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", delay=-1, gold=1)
    with pytest.raises(delivery.DeliveryError, match="guild and a purpose"):
        await delivery.send(db, ECONOMY, aria, yours.id, "", "purchase", gold=1)
    assert await gold_of(db, mine) == 100 and await holding(db, aria, "potion") == 2
    assert await db.scalar(select(func.count()).select_from(Shipment)) == 0


async def test_a_worn_item_a_hero_with_no_team_and_a_team_with_no_hero_are_refused(clock, db):
    mike, _mine, yours, aria, _cole = await two_teams(db)
    position = next(stack.position for stack, item in await inventory.stacks(db, aria) if item.key == "sword")
    await inventory.equip(db, aria, position)
    with pytest.raises(delivery.DeliveryError, match="take it off"):
        await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", items=[(position, 1)])
    loner = await service.create_hero(db, mike, "Loner", "fighter")
    with pytest.raises(delivery.DeliveryError, match="on a team"):
        await delivery.send(db, ECONOMY, loner, yours.id, "vanguard", "purchase", gold=1)
    empty = await service.create_team(db, mike, "Empty")
    with pytest.raises(delivery.DeliveryError, match="no hero"):
        await delivery.send(db, ECONOMY, aria, empty.id, "vanguard", "purchase", gold=1)
    with pytest.raises(delivery.DeliveryError, match="no hero"):
        await delivery.send_goods(db, empty.id, "vanguard", "reward", gold=1)


async def test_the_game_can_send_goods_it_makes(clock, db):
    _mike, mine, yours, _aria, _cole = await two_teams(db)
    parcel = await delivery.send_goods(db, yours.id, "vanguard", "reward", "quest-4", delay=HOUR, gold=25, items=[("potion", 3)])
    assert parcel.from_team_id is None and await gold_of(db, mine) == 100
    assert await delivery.contents(db, parcel) == {"gold": 25, "items": [("potion", 3)]}
    with pytest.raises(delivery.DeliveryError, match="no item"):
        await delivery.send_goods(db, yours.id, "vanguard", "reward", items=[("nothing", 1)])
    assert await db.scalar(select(func.count()).select_from(Shipment)) == 1, "the unknown item left no parcel behind"
    assert await db.scalar(select(func.count()).select_from(ShipmentItem)) == 1


# --- finding and arriving ----------------------------------------------------------------------------------------------

async def test_parcels_are_found_by_team_guild_purpose_ref_and_whether_they_have_arrived(clock, db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    soon = await delivery.send_goods(db, yours.id, "vanguard", "reward", "q1", delay=HOUR, gold=1)
    late = await delivery.send_goods(db, mine.id, "crown", "purchase", "q2", delay=3 * HOUR, gold=1)
    assert [p.id for p in await delivery.parcels(db)] == [soon.id, late.id]
    assert [p.id for p in await delivery.parcels(db, to_team_id=yours.id)] == [soon.id]
    assert [p.id for p in await delivery.parcels(db, guild="crown")] == [late.id]
    assert [p.id for p in await delivery.parcels(db, purpose="reward")] == [soon.id]
    assert [p.id for p in await delivery.parcels(db, ref="q2")] == [late.id]
    assert await delivery.parcels(db, arrived=True) == []
    clock.move(2 * HOUR)
    assert [p.id for p in await delivery.parcels(db, arrived=True)] == [soon.id]
    assert [p.id for p in await delivery.parcels(db, arrived=False)] == [late.id]


# --- collecting --------------------------------------------------------------------------------------------------------

async def test_a_hero_on_the_receiving_team_collects_a_parcel_that_has_arrived(clock, db):
    _mike, mine, yours, aria, cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", delay=HOUR, gold=40, items=[(0, 2)])
    parcel_id = parcel.id
    with pytest.raises(delivery.DeliveryError, match="not arrived"):
        await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    clock.move(HOUR)
    done = await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    assert (done.state, done.settled_at) == (COLLECTED, wallclock.timestamp())
    assert await gold_of(db, yours) == 40 and await holding(db, cole, "potion") == 2
    assert await delivery.parcels(db) == []
    with pytest.raises(delivery.DeliveryError, match="collected already"):
        await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    assert await gold_of(db, yours) == 40, "a parcel pays once"


async def test_only_the_receiving_team_can_collect_and_the_refusal_does_not_say_why(clock, db):
    mike, _mine, yours, aria, _cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", gold=10)
    loner = await service.create_hero(db, mike, "Loner", "fighter")
    for hero, parcel_id in ((aria, parcel.id), (loner, parcel.id), (aria, parcel.id + 100)):
        with pytest.raises(delivery.DeliveryError, match="no such parcel for you"):
            await delivery.collect(db, ECONOMY, NoStorage(), hero, parcel_id)


async def test_items_are_collected_into_the_games_storage(clock, db):
    _mike, _mine, yours, aria, cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", items=[(0, 2)])
    await delivery.collect(db, ECONOMY, TeamStorage(), cole, parcel.id)
    assert await holding(db, cole, "potion") == 0
    owner = await TeamStorage().owner(db, cole)
    assert [(item.key, stack.qty) for stack, item in await storage_service.stacks(db, owner)] == [("potion", 2)]


async def test_a_team_with_no_room_leaves_the_parcel_waiting(clock, db):
    _mike, _mine, yours, aria, cole = await two_teams(db)
    for _ in range(inventory.MAX_ITEMS):
        await stock(db, cole, "sword")  # equipment never stacks: Cole's pack is full
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", gold=40, items=[(0, 2)])
    parcel_id = parcel.id
    with pytest.raises(delivery.DeliveryError, match="no room"):
        await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    assert await gold_of(db, yours) == 0, "the gold paid before the items were tried came back"
    await db.refresh(parcel)
    assert parcel.state == TRANSIT and parcel.settled_at is None


# --- calling a parcel back ---------------------------------------------------------------------------------------------

async def test_a_cancelled_parcel_goes_back_to_its_sender(clock, db):
    _mike, mine, yours, aria, _cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", delay=HOUR, gold=40, items=[(0, 2)])
    done = await delivery.cancel(db, ECONOMY, NoStorage(), parcel.id)
    assert done.state == CANCELLED
    assert await gold_of(db, mine) == 100 and await holding(db, aria, "potion") == 2 and await gold_of(db, yours) == 0
    with pytest.raises(delivery.DeliveryError, match="settled already"):
        await delivery.cancel(db, ECONOMY, NoStorage(), parcel.id)


async def test_a_cancelled_game_parcel_vanishes(clock, db):
    _mike, _mine, yours, _aria, cole = await two_teams(db)
    parcel = await delivery.send_goods(db, yours.id, "vanguard", "reward", gold=25, items=[("potion", 3)])
    parcel_id = parcel.id
    assert (await delivery.cancel(db, ECONOMY, NoStorage(), parcel_id)).state == CANCELLED
    clock.move(HOUR)
    assert await gold_of(db, yours) == 0 and await holding(db, cole, "potion") == 0
    with pytest.raises(delivery.DeliveryError, match="no such parcel for you|not arrived"):
        await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    with pytest.raises(delivery.DeliveryError, match="no such parcel"):
        await delivery.cancel(db, ECONOMY, NoStorage(), parcel_id + 100)


async def test_a_parcel_collected_first_cannot_be_called_back(clock, db):
    _mike, mine, yours, aria, cole = await two_teams(db)
    parcel = await delivery.send(db, ECONOMY, aria, yours.id, "vanguard", "purchase", gold=40)
    parcel_id = parcel.id
    await delivery.collect(db, ECONOMY, NoStorage(), cole, parcel_id)
    with pytest.raises(delivery.DeliveryError, match="settled already"):
        await delivery.cancel(db, ECONOMY, NoStorage(), parcel_id)
    assert await gold_of(db, mine) == 60 and await gold_of(db, yours) == 40
