"""The housekeeping jobs for trading: ended listings, unanswered contract offers and running contracts, on every database."""

import pytest

from terraforma import housekeeping
from terraforma.contracts import service as contracts_service
from terraforma.contracts.models import ACTIVE, FULFILLED, OFFERED
from terraforma.contracts.terms import StandardTerms
from terraforma.marketplace import service as marketplace
from terraforma.marketplace.hooks import Marketplace
from terraforma.marketplace.models import EXPIRED, OPEN, Listing

from .test_contracts import ECONOMY, FEES, DAY, clock, two_sides  # noqa: F401
from .test_marketplace import a_market  # noqa: F401
from .test_trading import holding

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]


async def job(db, name):
    return await housekeeping.JOBS[name](db, 0, None)  # (the trading jobs read neither the clock argument nor the settings)


async def test_an_ended_listing_goes_back_to_its_seller_and_an_open_one_stays(clock, db):
    _mike, _co, _mine, _yours, aria, _cole = await a_market(db)
    old = await marketplace.post(db, ECONOMY, FEES, Marketplace(), aria, "vanguard", [(0, 1)], 10)
    old_id = old.id
    assert await job(db, "ended_listings") == 0
    clock.move(8 * DAY)
    fresh = await marketplace.post(db, ECONOMY, FEES, Marketplace(), aria, "vanguard", [(0, 1)], 10)
    assert await job(db, "ended_listings") == 1
    assert (await db.get(Listing, old_id)).state == EXPIRED and (await db.get(Listing, fresh.id)).state == OPEN
    assert await job(db, "ended_listings") == 0  # settled once


async def test_an_unanswered_offer_expires_and_a_running_contract_is_judged(clock, db):
    a, b, aria, cole = await two_sides(db)
    terms = StandardTerms()
    offer = await contracts_service.offer(db, ECONOMY, FEES, terms, aria, "vanguard", b.id, [("truce", {})], [("truce", {})], 100, 100, DAY)
    offer_id = offer.id
    clock.move(4 * DAY)
    assert await job(db, "unanswered_contract_offers") == 1
    assert (await db.get(type(offer), offer_id)).state != OFFERED
    fresh = await contracts_service.offer(db, ECONOMY, FEES, terms, aria, "vanguard", b.id, [("truce", {})], [("truce", {})], 100, 100, DAY)
    fresh_id = fresh.id
    await contracts_service.accept(db, ECONOMY, FEES, terms, cole, fresh_id)
    assert await job(db, "running_contracts") == 0  # still running
    clock.move(2 * DAY)
    assert await job(db, "running_contracts") == 1  # the truce held to the end
    assert (await db.get(type(offer), fresh_id)).state == FULFILLED
    assert ACTIVE != FULFILLED and await job(db, "running_contracts") == 0
