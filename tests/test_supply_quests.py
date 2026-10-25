"""Supply quests: a live settlement whose supply of a material falls below the game's floor gets a quest to restore it, the quest is taken back
when the gap closes, and the delivery fills the supply. On every database."""

import pytest
from sqlalchemy import select

from terraforma.fights.rules import Rules
from terraforma.models import advance_clock
from terraforma.quests import needs
from terraforma.quests.hooks import Needs
from terraforma.quests.needs_models import LIVE, PROPOSED, REJECTED, RETIRED, SUPPLY, QuestProposal
from terraforma.quests.weekly_models import QuestTemplate
from terraforma.realms import supply
from terraforma.realms.models import RUINS

from .test_realm_supply import a_town

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


class Hungry(Needs):
    """Asks for iron to stay at a fifth or more, in the "supply" slot."""

    def __init__(self, floor=20, slot="supply", approval=True):
        self.floor, self.slot, self.approval = floor, slot, approval

    def supply_floor(self, settlement, material):
        return self.floor if material.key == "iron" else 0

    def supply_slot(self, settlement, material):
        return self.slot

    def needs_approval(self, band):
        return self.approval


async def run(db, hooks):
    return await needs.run(db, hooks, Rules())


async def test_a_supply_below_the_floor_makes_one_proposal(db):
    town = await a_town(db)  # iron 10 of 50 = 20%: not below 20
    assert await run(db, Hungry()) == []
    await supply.set_cut(db, town, "iron", 100)  # nothing arrives; one used up each tick
    await advance_clock(db, town.world_id, 2)  # 8 of 50 = 16%
    made = await run(db, Hungry())
    assert len(made) == 1
    one = made[0]
    assert (one.reason, one.slot, one.status) == (SUPPLY, "supply", PROPOSED)
    assert one.spec["supply"] == {"settlement": "capital", "material": "iron", "amount": 17}, "half of 50, less the 8 there"
    assert "Iron" in one.name and town.name in one.name
    assert await run(db, Hungry()) == [], "a gap is not answered twice"


async def test_no_floor_or_no_slot_makes_nothing(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 5)
    assert await run(db, Needs()) == [], "the default asks for nothing"
    assert await run(db, Hungry(slot="")) == [], "no slot, no quest"
    assert await run(db, Hungry(floor=0)) == []
    assert await db.scalar(select(QuestProposal.id)) is None


async def test_the_floor_is_read_at_the_world_clock_without_changing_the_level(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 3)
    await run(db, Hungry())
    row = (await db.scalars(select(supply.SettlementSupply))).first()
    assert row.as_of_tick < 3, "the run only looked"


async def test_approval_makes_the_template_and_a_recovered_supply_retires_it(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 5)
    (one,) = await run(db, Hungry(approval=False))
    assert one.status == LIVE
    template = await db.get(QuestTemplate, one.template_id)
    assert template.active and template.slot == "supply" and template.spec["supply"]["material"] == "iron"
    await supply.add(db, town, "iron", 40)  # back above the floor
    assert await run(db, Hungry()) == []
    await db.refresh(one), await db.refresh(template)
    assert one.status == RETIRED and one.open is None and not template.active


async def test_a_waiting_proposal_is_rejected_when_the_gap_closes_and_a_new_gap_makes_a_new_one(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 5)
    (first,) = await run(db, Hungry())
    await supply.add(db, town, "iron", 40)
    await run(db, Hungry())
    await db.refresh(first)
    assert first.status == REJECTED
    await supply.take(db, town, "iron", 100)
    (second,) = await run(db, Hungry())
    assert second.id != first.id and second.band == first.band


async def test_a_settlement_that_fell_is_not_asked_for(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 5)
    (one,) = await run(db, Hungry())
    town.state = RUINS
    await db.flush()
    assert await run(db, Hungry()) == []
    await db.refresh(one)
    assert one.status == REJECTED


async def test_delivery_fills_the_supply_up_to_the_capacity(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 100)
    await advance_clock(db, town.world_id, 5)
    (one,) = await run(db, Hungry())
    before = (await supply.supply_of(db, town, "iron")).level
    assert await needs.deliver(db, one.spec) == one.spec["supply"]["amount"]
    assert (await supply.supply_of(db, town, "iron")).level == before + one.spec["supply"]["amount"]
    room = 50 - (await supply.supply_of(db, town, "iron")).level
    assert await needs.deliver(db, one.spec, 1000) == room, "no more than fits"
    assert (await supply.supply_of(db, town, "iron")).level == 50


async def test_delivery_to_a_fallen_settlement_or_a_quest_without_goods_does_nothing(db):
    town = await a_town(db)
    spec = {"supply": {"settlement": "capital", "material": "iron", "amount": 5}}
    town.state = RUINS
    await db.flush()
    assert await needs.deliver(db, spec) == 0
    with pytest.raises(needs.NeedsError):
        await needs.deliver(db, {"bosses": []})
