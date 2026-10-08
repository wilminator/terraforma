"""Talking in a real browser: the nearby list starts a conversation, the talk panel plays it, and the shop and the inn work from it.
The example game stands three people next to where heroes start: Old Tom (gives gold), Hana the Keeper (a shop) and Bede the Innkeeper."""

from playwright.sync_api import expect

from .test_heroes import make_team, open_game, tab


def start(page, live_server):
    """A logged-in player with a team of one hero, standing next to the example's NPCs."""
    open_game(page, live_server)
    make_team(page, "Alpha")


def talk_to(page, name):
    tab(page, "Nearby")
    page.locator("#nearby-action").select_option("talk")
    page.get_by_role("button", name="Refresh").click()
    page.get_by_role("button", name=f"Talk to {name}").click()
    expect(page.locator("#panel-dialog")).to_be_visible()
    expect(page.locator("#dialog-speaker")).to_have_text(name)


def test_the_nearby_list_offers_the_people_next_to_the_hero(page, live_server):
    start(page, live_server)
    tab(page, "Nearby")
    page.locator("#nearby-action").select_option("talk")
    page.get_by_role("button", name="Refresh").click()
    people = page.locator("#panel-nearby [data-kind='npc']")
    expect(people).to_have_count(3)
    expect(people).to_contain_text(["Bede the Innkeeper", "Hana the Keeper", "Old Tom"])
    page.locator("#nearby-action").select_option("fight")
    expect(page.get_by_role("button", name="Talk to Old Tom")).to_have_count(0)  # only talking is offered for now


def test_a_conversation_plays_to_its_end_and_is_put_away(page, live_server):
    start(page, live_server)
    tab(page, "Talk")
    expect(page.locator("#dialog-none")).to_be_visible()  # nobody yet
    talk_to(page, "Old Tom")
    expect(page.locator(".transcript")).to_contain_text("Take this for the road")
    expect(page.locator(".transcript")).to_contain_text("He presses 30 gold into your hand.")
    expect(page.locator("#dialog-over")).to_be_visible()
    page.locator("#dialog-close").click()
    expect(page.locator("#dialog-none")).to_be_visible()


def test_the_shop_sells_and_buys_with_the_servers_prices_and_the_dialog_goes_on_when_done(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    start(page, live_server)
    talk_to(page, "Hana the Keeper")
    expect(page.locator(".transcript")).to_contain_text("Fresh stock, traveller.")
    expect(page.locator("#shop-gold")).to_have_text("Your gold: 0")
    expect(page.locator("[data-ware='Herb']")).to_contain_text("12 gold")
    expect(page.locator("[data-ware='Potion']")).to_contain_text("40 gold")
    page.get_by_role("button", name="Buy Herb").click()
    expect(page.locator("#panel-dialog .problem")).to_have_text("that costs more gold than there is")

    talk_to_tom_for_gold(page)
    talk_to(page, "Hana the Keeper")
    page.get_by_label("How many Herb to buy").fill("2")
    page.get_by_role("button", name="Buy Herb").click()
    expect(page.locator("#shop-note")).to_have_text("Bought 2 for 24 gold.")
    expect(page.locator("#shop-gold")).to_have_text("Your gold: 6")

    expect(page.get_by_text("You could sell")).to_have_count(0)  # (this keeper only sells: `vend` never buys)

    page.locator("#shop-done").click()  # leaving the shop goes on with what the keeper says next
    expect(page.locator(".transcript")).to_contain_text("Come again soon.")
    expect(page.locator("#dialog-over")).to_be_visible()


def talk_to_tom_for_gold(page):
    talk_to(page, "Old Tom")
    page.locator("#dialog-close").click()


def test_the_inn_asks_yes_or_no_and_rests_the_heroes_for_gold(page, live_server):
    start(page, live_server)
    talk_to_tom_for_gold(page)
    talk_to(page, "Bede the Innkeeper")
    expect(page.locator(".transcript")).to_contain_text("A bed is 10 gold. Rest?")
    expect(page.locator("[data-choice]")).to_have_text(["Yes", "No"])
    page.locator("[data-choice='0']").click()  # the heroes rest and pay; the dialog goes on to a Next
    page.locator("#dialog-next").click()
    expect(page.locator(".transcript")).to_contain_text("Sleep well.")
    expect(page.locator("#dialog-over")).to_be_visible()
    page.locator("#dialog-close").click()

    talk_to(page, "Hana the Keeper")  # the gold went to the inn
    expect(page.locator("#shop-gold")).to_have_text("Your gold: 20")


def test_saying_no_at_the_inn_costs_nothing(page, live_server):
    start(page, live_server)
    talk_to_tom_for_gold(page)
    talk_to(page, "Bede the Innkeeper")
    page.locator("[data-choice='1']").click()
    expect(page.locator(".transcript")).to_contain_text("Another time.")
    page.locator("#dialog-close").click()
    talk_to(page, "Hana the Keeper")
    expect(page.locator("#shop-gold")).to_have_text("Your gold: 30")


def test_a_question_can_be_cancelled_and_the_hero_can_walk_away(page, live_server):
    start(page, live_server)
    talk_to(page, "Bede the Innkeeper")
    expect(page.locator("#dialog-cancel")).to_be_visible()
    page.locator("#dialog-cancel").click()
    expect(page.locator("#dialog-over")).to_be_visible()  # (the dialog's own text decides where a cancel goes: here it ends)
    page.locator("#dialog-close").click()

    talk_to(page, "Hana the Keeper")
    page.locator("#dialog-leave").click()
    expect(page.locator("#dialog-none")).to_be_visible()


def test_a_conversation_is_picked_up_again_after_a_reload(page, live_server):
    start(page, live_server)
    talk_to(page, "Hana the Keeper")
    page.reload()
    expect(page.locator("#who")).to_be_visible()
    tab(page, "Talk")
    expect(page.locator("#dialog-speaker")).to_have_text("Hana the Keeper")
    expect(page.locator("#shop")).to_be_visible()
