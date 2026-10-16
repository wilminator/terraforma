"""The guild fee rule: the base price to everyone by default, a discount by rank and at merchant guilds, on every database."""

import pytest

from terraforma.game import Game
from terraforma.guild import ranks
from terraforma.guild.fees import Fees, RankFees

from .test_trading import account, team_with

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

DISCOUNTS = (0, 10, 20, 30)


async def teams(db):
    mike = await account(db)
    mine, _ = await team_with(db, mike, "Vanguard", ["Aria"])
    yours, _ = await team_with(db, mike, "Rearguard", ["Cole"])
    return mine, yours


async def test_the_default_charges_everyone_the_base_price(db):
    mine, _yours = await teams(db)
    await ranks.set_rank(db, mine.id, "vanguard", 5)
    assert await Fees().quote(db, mine.id, "vanguard", 120) == 120
    assert await Fees().quote(db, mine.id, "vanguard", 120, kind="contract") == 120
    assert isinstance(Game("test").fees, Fees)


async def test_a_higher_rank_pays_less_and_the_last_discount_repeats(db):
    mine, _yours = await teams(db)
    fees = RankFees(DISCOUNTS)
    paid = []
    for rank in range(6):
        await ranks.set_rank(db, mine.id, "vanguard", rank)
        paid.append(await fees.quote(db, mine.id, "vanguard", 100))
    assert paid == [100, 90, 80, 70, 70, 70]


async def test_a_team_with_no_standing_pays_the_rank_zero_price(db):
    mine, _yours = await teams(db)
    assert await RankFees((5, 50)).quote(db, mine.id, "vanguard", 100) == 95
    assert await RankFees().quote(db, mine.id, "vanguard", 100) == 100, "no discounts listed: the base price"


async def test_rank_counts_for_the_team_and_the_guild_it_was_earned_in(db):
    mine, yours = await teams(db)
    await ranks.set_rank(db, mine.id, "vanguard", 3)
    fees = RankFees(DISCOUNTS)
    assert await fees.quote(db, mine.id, "vanguard", 100) == 70
    assert await fees.quote(db, yours.id, "vanguard", 100) == 100, "another team"
    assert await fees.quote(db, mine.id, "crown", 100) == 100, "another guild"


async def test_a_merchant_guild_takes_a_further_cut_off(db):
    mine, yours = await teams(db)
    await ranks.set_rank(db, mine.id, "traders", 1)
    fees = RankFees(DISCOUNTS, {"traders": 20})
    assert await fees.quote(db, mine.id, "traders", 100) == 72, "10% for the rank, then 20% for the merchants"
    assert await fees.quote(db, yours.id, "traders", 100) == 80, "the merchant cut is for everyone at that guild"
    assert await fees.quote(db, mine.id, "vanguard", 100) == 100, "and only at that guild"


async def test_prices_are_whole_gold_rounded_up_and_never_above_the_base(db):
    mine, _yours = await teams(db)
    await ranks.set_rank(db, mine.id, "vanguard", 1)
    fees = RankFees(DISCOUNTS)
    assert await fees.quote(db, mine.id, "vanguard", 33) == 30, "29.7 is charged as 30"
    assert await fees.quote(db, mine.id, "vanguard", 1) == 1
    assert await fees.quote(db, mine.id, "vanguard", 0) == 0
    assert await RankFees((100,)).quote(db, mine.id, "vanguard", 50) == 0, "a full discount is free"


async def test_bad_numbers_are_refused(db):
    mine, _yours = await teams(db)
    for build in (lambda: RankFees((101,)), lambda: RankFees((-1,)), lambda: RankFees((), {"traders": 150})):
        with pytest.raises(ValueError, match="percentage"):
            build()
    with pytest.raises(ValueError, match="zero or more"):
        await RankFees(DISCOUNTS).quote(db, mine.id, "vanguard", -1)


async def test_a_game_can_price_each_kind_of_service_its_own_way(db):
    mine, _yours = await teams(db)

    class Haggling(Fees):
        async def quote(self, session, team_id, guild, base, kind=""):
            return base if kind == "contract" else base // 2

    game = Game("test", fees=Haggling())
    assert await game.fees.quote(db, mine.id, "vanguard", 100, kind="contract") == 100
    assert await game.fees.quote(db, mine.id, "vanguard", 100, kind="listing") == 50
