"""A guild's marketplace in a real browser: an NPC whose dialog holds the `market` tag opens it, the hero lists goods from their pack, the
listing shows in the search, and what the server refuses is shown in its words (the calls behind it are tests/test_marketplace_routes.py).
The example game has no market NPC, so the keeper's text is changed to hold one."""

from playwright.sync_api import expect
from sqlalchemy import select, update

from terraforma.heroes import inventory
from terraforma.heroes.models import Hero
from terraforma.npcs.models import Npc

from .test_talk import start, talk_to, talk_to_tom_for_gold

BOARD = "The board is up.`market,vanguard,leave`Come again soon.`jump,end``label,leave`Another time."


def open_the_board(live_server):
    async def work(db):
        await db.execute(update(Npc).where(Npc.key == "keeper").values(dialog=BOARD))

    live_server.in_db(work)


def stock_the_pack(live_server, key, qty):
    async def work(db):
        hero = await db.scalar(select(Hero).limit(1))
        assert await inventory.add_item(db, hero, key, qty) == 0

    live_server.in_db(work)


def test_a_hero_lists_goods_at_the_guild_and_cannot_buy_their_own_listing(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    open_the_board(live_server)
    start(page, live_server)
    stock_the_pack(live_server, "potion", 3)
    talk_to_tom_for_gold(page)  # the posting fee comes out of the team's gold
    talk_to(page, "Hana the Keeper")
    expect(page.locator("#market")).to_contain_text("Marketplace: vanguard")
    expect(page.locator("#market-none")).to_be_visible()

    page.get_by_label("How many to sell").fill("2")
    page.get_by_label("Price in gold").fill("20")
    page.locator("#market-post-button").click()
    expect(page.locator("#market-note")).to_have_text("Listed at vanguard.")
    listing = page.locator("[data-listing]")
    expect(listing).to_have_count(1)
    expect(listing).to_contain_text("potion ×2")
    expect(listing).to_contain_text("20 gold")

    page.get_by_role("button", name="Buy listing 1").click()
    expect(page.locator("#panel-dialog .problem")).to_have_text("a team cannot buy its own listing")
    expect(listing).to_have_count(1)

    page.get_by_label("Search the listings").fill("sword")
    page.get_by_role("button", name="Search").click()
    expect(page.locator("#market-none")).to_be_visible()

    page.locator("#market-done").click()  # leaving the board goes on with what the keeper says next
    expect(page.locator(".transcript")).to_contain_text("Come again soon.")
    expect(page.locator("#dialog-over")).to_be_visible()


def test_an_auction_takes_a_bid_a_barter_has_no_buy_button_and_a_parcel_is_collected(page, live_server):
    open_the_board(live_server)
    start(page, live_server)
    page.route("**/api/market/listings*", lambda route: route.fulfill(json=[
        {"id": 7, "guild": "vanguard", "seller": "Rivals", "price_gold": 30, "price_tokens": 0, "asks": [], "goods": [["potion", 1]],
         "expires_at": 4_000_000_000, "kind": "auction", "bid_gold": 40},
        {"id": 8, "guild": "vanguard", "seller": "Rivals", "price_gold": 0, "price_tokens": 0, "asks": [["herb", 2]], "goods": [["dagger", 1]],
         "expires_at": 4_000_000_000, "kind": "fixed", "bid_gold": 0},
    ]))
    page.route("**/parcels", lambda route: route.fulfill(json=[
        {"id": 3, "guild": "vanguard", "purpose": "market", "state": "transit", "arrives_at": 1, "gold": 0, "items": [["herb", 2]]},
        {"id": 4, "guild": "elsewhere", "purpose": "market", "state": "transit", "arrives_at": 1, "gold": 5, "items": []},
    ]))
    bids, collected = [], []
    page.route("**/listings/7/bid", lambda route: (bids.append(route.request.post_data_json), route.fulfill(json={"id": 7, "bid_gold": 45, "expires_at": 4_000_000_000}))[1])
    page.route("**/parcels/3/collect", lambda route: (collected.append(route.request.post_data_json), route.fulfill(json={"id": 3}))[1])
    talk_to(page, "Hana the Keeper")

    expect(page.locator("[data-listing='7']")).to_contain_text("auction, now 40 gold")
    expect(page.locator("[data-listing='8']")).to_contain_text("asks herb ×2")
    expect(page.get_by_role("button", name="Buy listing 8")).to_have_count(0)
    page.get_by_label("Your bid for listing 7").fill("45")
    page.get_by_role("button", name="Bid on listing 7").click()
    expect(page.locator("#market-note")).to_have_text("Your bid of 45 gold is the highest.")
    assert bids == [{"amount": 45, "to_guild": "vanguard"}]

    expect(page.locator("[data-parcel]")).to_have_count(1)  # the parcel at another guild is not shown here
    page.get_by_role("button", name="Collect parcel 3").click()
    expect(page.locator("#market-note")).to_have_text("Collected.")
    assert collected == [{}]
