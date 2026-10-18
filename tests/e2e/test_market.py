"""The Market panel in a real browser: finding a listing, buying it, bidding on an auction, taking back the team's own listing, and
collecting a parcel (the calls behind it are tests/test_marketplace_routes.py). A seller (Zed) sets listings up through the calls, then
the page is Mike's."""

from playwright.sync_api import expect
from sqlalchemy import select, update

from terraforma import wallclock
from terraforma.delivery.models import Shipment
from terraforma.economy import TeamGold
from terraforma.heroes.models import TeamMember

from .test_heroes import open_game, tab
from .test_pack import Player, fill


def credit(live_server, hero_id, gold):
    async def work(db):
        team_id = await db.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero_id))
        await TeamGold().credit_team(db, team_id, gold)

    live_server.in_db(work)


def start(page, live_server):
    """Zed's Zara lists a potion for 40 gold and a herb as an auction from 10; then Mike's Aria (100 gold) is on the Market panel."""
    live_server.make_account("Zed")
    zed = Player(page, "Zed")
    zed.post("/api/teams", {"name": "Rearguard", "heroes": [{"name": "Zara", "job": "fighter"}]})
    zara = zed.heroes()["Zara"]
    fill(live_server, zara, items=[("potion", 1), ("herb", 1)])
    credit(live_server, zara, 50)
    zed.post(f"/api/heroes/{zara}/listings", {"guild": "vanguard", "items": [{"position": 0, "qty": 1}], "price_gold": 40})
    zed.post(f"/api/heroes/{zara}/auctions", {"guild": "vanguard", "items": [{"position": 0, "qty": 1}], "start_gold": 10})
    page.context.clear_cookies()  # (Zed's login is not the page's)
    open_game(page, live_server)
    mike = Player(page, "Mike")
    mike.post("/api/teams", {"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]})
    aria = mike.heroes()["Aria"]
    credit(live_server, aria, 100)
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Market")
    expect(page.locator("#market-rows li")).to_have_count(2)
    return mike, aria


def test_the_search_narrows_the_listings(page, live_server):
    start(page, live_server)
    page.locator("#market-text").fill("herb")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#market-rows li")).to_have_count(1)
    expect(page.locator("[data-listing]")).to_contain_text("auction from 10 gold")
    page.locator("#market-text").fill("sword")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#market-none")).to_have_text("Nothing is listed.")


def test_a_listing_is_bought_and_its_parcel_collected_when_it_has_arrived(page, live_server):
    mike, aria = start(page, live_server)
    page.locator("#market-text").fill("potion")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#market-rows li")).to_have_count(1)
    expect(page.locator("[data-listing]")).to_contain_text("1 × potion at vanguard, from Rearguard · 40 gold")
    page.get_by_role("button", name=" ".join(["Buy listing", page.locator("[data-listing]").get_attribute("data-listing")])).click()
    expect(page.locator("#market-note")).to_have_text("Bought: the goods are on their way as a parcel.")
    expect(page.locator("#market-none")).to_be_visible()
    expect(page.locator("#market-parcels li")).to_have_count(1)

    async def arrive(db):
        await db.execute(update(Shipment).values(arrives_at=wallclock.timestamp() - 1))  # (the delay has passed)

    live_server.in_db(arrive)
    page.get_by_role("button", name="Search").click()
    page.get_by_role("button", name="Collect").click()
    expect(page.locator("#market-note")).to_have_text("Collected.")
    expect(page.locator("#market-parcels-none")).to_be_visible()
    held = page.request.get(f"/api/heroes/{aria}/inventory").json()["items"]
    assert [(stack["item"], stack["qty"]) for stack in held] == [("potion", 1)]


def test_a_bid_is_placed_and_a_refusal_is_shown_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    start(page, live_server)
    page.locator("#market-text").fill("herb")
    page.get_by_role("button", name="Search").click()
    row = page.locator("[data-listing]")
    expect(row).to_have_count(1)
    bid = row.locator("input[type=number]")
    bid.fill("5")  # (below the starting bid)
    row.get_by_role("button", name="Bid").click()
    expect(page.locator("#market-problem")).not_to_be_empty()
    bid.fill("12")
    row.get_by_role("button", name="Bid").click()
    expect(page.locator("#market-note")).to_have_text("Bid placed.")
    expect(page.locator("[data-listing]")).to_contain_text("auction, bid 12 gold")


def test_a_team_takes_back_its_own_listing(page, live_server):
    mike, aria = start(page, live_server)
    fill(live_server, aria, items=[("antidote", 1)])
    own = mike.post(f"/api/heroes/{aria}/listings", {"guild": "vanguard", "items": [{"position": 0, "qty": 1}], "price_gold": 5})
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#market-rows li")).to_have_count(3)
    expect(page.get_by_role("button", name=f"Buy listing {own['id']}")).to_have_count(0)  # (not one's own)
    page.get_by_role("button", name=f"Take back listing {own['id']}").click()
    expect(page.locator("#market-note")).to_have_text("Listing taken back.")
    expect(page.locator("#market-rows li")).to_have_count(2)
