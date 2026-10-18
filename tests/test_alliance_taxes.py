"""Alliance taxes: the share of a member team's fight and sales gold that its alliance keeps, paid into the treasury."""

import pytest
from sqlalchemy import select

from terraforma.alliances import ballots, service, treasury
from terraforma.alliances.hooks import Alliances
from terraforma.alliances.models import Alliance
from terraforma.alliances.taxes import AllianceTaxman
from terraforma.economy import TeamGold, credit_fight_gold
from terraforma.marketplace import service as market
from terraforma.guild.fees import Fees
from terraforma.marketplace.hooks import Marketplace
from terraforma.storage.hooks import NoStorage

from .test_alliance_treasury import gold, purse, setup
from .test_economy import a_fight
from .test_marketplace import DAY, a_market, clock, gold_of  # noqa: F401  (the fixture)

pytestmark = [pytest.mark.social, pytest.mark.anyio]

RULES = Alliances()


def taxing() -> TeamGold:
    economy = TeamGold()
    economy.taxman = AllianceTaxman()
    return economy


async def rates(db, alliance):
    row = (await db.execute(select(Alliance.fight_tax, Alliance.sales_tax).where(Alliance.id == alliance.id))).one()
    return tuple(row)


# --- setting the rates ----------------------------------------------------------------------------------------------------------------

async def test_the_commander_sets_the_two_rates_apart(db):
    alliance, (lead, one, two) = await setup(db)
    assert await service.set_fight_tax(db, RULES, alliance, lead.id, 10) == 10
    assert await service.set_sales_tax(db, RULES, alliance, lead.id, 5) == 5
    assert await rates(db, alliance) == (10, 5)
    assert (await service.view(db, RULES, alliance, one.id))["fight_tax"] == 10


async def test_a_rate_is_a_whole_percent_up_to_the_games_limit(db):
    alliance, (lead, one, two) = await setup(db)
    for percent in (-1, RULES.max_tax_percent + 1):
        with pytest.raises(treasury.TreasuryError, match="0 to 50 percent"):
            await service.set_fight_tax(db, RULES, alliance, lead.id, percent)
    await service.set_fight_tax(db, RULES, alliance, lead.id, RULES.max_tax_percent)
    await service.set_fight_tax(db, RULES, alliance, lead.id, 0)
    assert await rates(db, alliance) == (0, 0)


async def test_only_a_role_that_holds_the_action_sets_a_rate(db):
    alliance, (lead, one, two) = await setup(db)
    with pytest.raises(service.Forbidden):
        await service.set_fight_tax(db, RULES, alliance, one.id, 5)
    await service.appoint(db, RULES, alliance, lead.id, one.id, "treasurer")
    await service.set_sales_tax(db, RULES, alliance, one.id, 5)
    assert await rates(db, alliance) == (0, 5)


async def test_a_rule_can_ask_for_a_vote_before_a_rate_is_set(db):
    alliance, (lead, one, two) = await setup(db)
    await service.set_rule(db, RULES, alliance, lead.id, "set_fight_tax", "", 60)
    with pytest.raises(service.NeedsVote):
        await service.set_fight_tax(db, RULES, alliance, lead.id, 20)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "set_fight_tax", {"percent": 20})
    assert ballot.title == "Set fight tax 20"
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert ballot.result["executed"] and await rates(db, alliance) == (20, 0)


# --- what is taxed ------------------------------------------------------------------------------------------------------------------

async def test_a_members_fight_gold_is_taxed_and_the_tax_fills_the_treasury(db):
    alliance, (lead, one, two) = await setup(db)
    await service.set_fight_tax(db, RULES, alliance, lead.id, 10)
    await service.set_sales_tax(db, RULES, alliance, lead.id, 50)
    record = await a_fight(db)
    await credit_fight_gold(db, taxing(), record, [(one.id, 255), (two.id, 9)])
    assert await gold(db, one, two) == [1000 + 255 - 25, 1000 + 9 - 0], "10% rounded down: 25 of 255, nothing of 9"
    assert await purse(db, alliance) == 25


async def test_only_the_alliances_own_members_are_taxed(db):
    alliance, (lead, one, two) = await setup(db)
    await service.set_fight_tax(db, RULES, alliance, lead.id, 20)
    await service.remove(db, RULES, alliance, lead.id, two.id)
    record = await a_fight(db)
    await credit_fight_gold(db, taxing(), record, [(two.id, 100)])
    assert await gold(db, two) == [1100] and await purse(db, alliance) == 0, "a team that left earns untaxed"


async def test_without_a_taxman_nothing_is_taxed(db):
    alliance, (lead, one, two) = await setup(db)
    await service.set_fight_tax(db, RULES, alliance, lead.id, 20)
    record = await a_fight(db)
    await credit_fight_gold(db, TeamGold(), record, [(one.id, 100)])
    assert await gold(db, one) == [1100] and await purse(db, alliance) == 0


async def test_a_team_in_two_alliances_pays_each_but_never_more_than_it_earned(db):
    alliance, (lead, one, two) = await setup(db)
    other = await service.found(db, RULES, two, "Rivals")
    await service.invite(db, RULES, other, two.id, one.id)
    await service.accept(db, RULES, one, other.id)
    await service.set_fight_tax(db, RULES, alliance, lead.id, 50)
    await service.set_fight_tax(db, RULES, other, two.id, 50)
    record = await a_fight(db)
    await credit_fight_gold(db, taxing(), record, [(one.id, 101)])
    assert (await purse(db, alliance), await purse(db, other)) == (50, 50)
    assert await gold(db, one) == [1000 + 1]


async def test_a_sale_on_the_market_is_taxed_when_the_seller_is_a_member(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    economy = taxing()
    pact = await service.found(db, RULES, mine, "Pact")
    await service.set_sales_tax(db, RULES, pact, mine.id, 10)
    await service.set_fight_tax(db, RULES, pact, mine.id, 50)
    listing = await market.post(db, economy, Fees(), Marketplace(), aria, "vanguard", [(0, 2)], 100)
    await market.buy(db, economy, Fees(), Marketplace(), cole, listing.id, "vanguard")
    assert await gold_of(db, mine) == 100 - 5 + 90, "paid the 5 fee to post; the sale of 100 gave 90 after the 10% sales tax"
    assert await gold_of(db, yours) == 100
    assert await purse(db, pact) == 10, "the fight tax doesn't touch a sale"


async def test_a_buyer_in_the_alliance_is_not_taxed_for_buying(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    economy = taxing()
    pact = await service.found(db, RULES, yours, "Pact")
    await service.set_sales_tax(db, RULES, pact, yours.id, 50)
    listing = await market.post(db, economy, Fees(), Marketplace(), aria, "vanguard", [(0, 2)], 100)
    await market.buy(db, economy, Fees(), Marketplace(), cole, listing.id, "vanguard")
    assert await gold_of(db, mine) == 195 and await purse(db, pact) == 0


async def test_a_won_auction_is_taxed_like_a_sale(clock, db):
    _mike, _cole_owner, mine, yours, aria, cole = await a_market(db)
    economy = taxing()
    pact = await service.found(db, RULES, mine, "Pact")
    await service.set_sales_tax(db, RULES, pact, mine.id, 25)
    listing = await market.post(db, economy, Fees(), Marketplace(), aria, "vanguard", [(0, 2)], 100, auction=True)
    await market.bid(db, economy, Fees(), Marketplace(), NoStorage(), cole, listing.id, 120, "vanguard")
    clock.move(2 * DAY)
    assert await market.expire_due(db, economy, NoStorage()) == 1
    assert await gold_of(db, mine) == 95 + 90 and await purse(db, pact) == 30
