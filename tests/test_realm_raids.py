"""Raids: a game says which settlements of a realm are raided as time passes; a raid cuts a supply for a while, posts to the bulletin and ends by itself."""

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.models import advance_clock
from terraforma.realms import news, strength, supply
from terraforma.realms.hooks import Raid, Realms
from terraforma.realms.models import Realm, Settlement
from terraforma.realms.service import RealmError

from .test_realm_supply import with_supply

pytestmark = [pytest.mark.anyio, pytest.mark.platform]


async def a_town(db):
    """'capital' keeps iron (10 of 50, +4 -1 a tick), hide (20 of 20, steady); ardent is its realm."""
    await load_content(db, with_supply())
    return await db.scalar(select(Settlement).where(Settlement.key == "capital"))


async def test_a_cut_that_lasts_so_many_ticks_ends_by_itself(db):
    town = await a_town(db)
    row = await supply.set_cut(db, town, "iron", 100, ticks=5)
    assert (row.cut, row.cut_until) == (100, 5)
    await advance_clock(db, town.world_id, 3)  # three ticks at a full cut: only the use goes on (-1 a tick)
    assert (await supply.supply_of(db, town, "iron")).level == 10 - 3
    await advance_clock(db, town.world_id, 6)  # two more at the cut (-2), then four ticks back to normal (+3 each)
    row = await supply.supply_of(db, town, "iron")
    assert row.level == 10 - 3 - 2 + 4 * 3 and (row.cut, row.cut_until) == (0, 0)


async def test_the_level_is_the_same_whether_it_is_read_part_way_or_once_at_the_end(db):
    town = await a_town(db)
    await supply.set_cut(db, town, "iron", 50, ticks=4)
    row = (await supply.find(db, town.world_id, "capital", "iron"))[1]
    await advance_clock(db, town.world_id, 10)
    assert supply.level_at(row, 10) == 10 + (4 * (4 * 50 // 100) - 4 * 1) + 6 * 3, "four ticks at half the rate (+2, -1), six at the full (+3)"


async def test_a_cut_with_no_ticks_lasts_until_it_is_changed_and_zero_clears_it(db):
    town = await a_town(db)
    assert (await supply.set_cut(db, town, "iron", 30)).cut_until == 0
    await advance_clock(db, town.world_id, 100)
    assert (await supply.supply_of(db, town, "iron")).cut == 30
    assert (await supply.set_cut(db, town, "iron", 0, ticks=9)).cut_until == 0, "no cut, no end"
    with pytest.raises(RealmError, match="at least one"):
        await supply.set_cut(db, town, "iron", 10, ticks=0)


class Raiding(Realms):
    """Every judged moment, the first live settlement is raided for 5 ticks (iron cut by 100), with a line for the bulletin; $seen keeps what it was asked."""

    seen: list = []

    def raids(self, realm, settlements, rng, elapsed):
        self.seen.append((realm.key, [each.key for each in settlements], elapsed, rng.random()))
        return [Raid(settlements[0], "iron", 100, 5, "Raiders struck Ardent City."), Raid(settlements[0], "gold", 50, 5, "never posted")] if settlements else []


async def test_a_raid_cuts_the_supply_posts_a_line_and_a_raid_on_a_missing_supply_is_skipped(db):
    town = await a_town(db)
    realm = await db.scalar(select(Realm).where(Realm.key == "ardent"))
    hooks = Raiding()
    hooks.seen = []
    assert await strength.judge(db, hooks) == 0 and hooks.seen == [], "no time has passed: no raid is asked for"
    await advance_clock(db, town.world_id, 2)
    await strength.judge(db, hooks)
    assert [(key, towns, elapsed) for key, towns, elapsed, _ in hooks.seen] == [("ardent", ["capital"], 2)]
    row = await supply.supply_of(db, town, "iron")
    assert (row.cut, row.cut_until) == (100, 2 + 5)
    assert [each.text for each in await news.latest(db, realm.world_id, 5)] == ["Raiders struck Ardent City."]
    assert (await news.latest(db, realm.world_id, 1))[0].kind == "raid"


async def test_the_stream_a_raid_is_drawn_from_is_repeatable_for_the_same_world_and_moment(db):
    town = await a_town(db)
    first, second = Raiding(), Raiding()
    first.seen, second.seen = [], []
    await advance_clock(db, town.world_id, 3)
    await strength.judge(db, first)
    realm = await db.scalar(select(Realm).where(Realm.key == "ardent"))
    realm.as_of_tick = 0  # the same moment looked at again
    await db.flush()
    await strength.judge(db, second)
    assert first.seen[0][3] == second.seen[0][3]
