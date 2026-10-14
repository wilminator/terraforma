"""The monsters page, in a real browser: someone who is not logged in joins the queue, is seated when a player's fight starts, and commands the
monsters with the fight scene the game uses, fed by the guest's calls and socket. The example game takes guests (its rules say so). The player
is Mike, whose hero fights a slime in the marsh of the hub (see test_fight); the guest has a browser of its own."""

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import expect

from terraforma.fights import live
from terraforma.fights.models import FightRecord

from .test_fight import SLOW, command, into_the_marsh, settled, view_of
from .test_menu import menu
from ..test_monster_rewards import set_hp


def queued(guest):
    """A guest on the monsters page, in the queue."""
    guest.goto("/monsters/")
    guest.locator("#monsters-join").click()
    expect(guest.locator("#monsters-place")).to_contain_text("number 1")


def seated(page, live_server, new_page):
    """A guest waits; Mike's hero walks into the marsh and the fight seats it. Returns the guest's page and the fight's number."""
    guest = new_page()
    queued(guest)
    fight_id = into_the_marsh(page, live_server)
    guest.reload()  # (the page asks where it stands as it opens: the fight is shown at once, not at the next check-in)
    stage = guest.locator("#stage")
    expect(stage).to_have_attribute("data-scene", "fight", timeout=SLOW)
    expect(stage).to_have_attribute("data-link", "open", timeout=SLOW)
    expect(stage).to_have_attribute("data-menu", "commands", timeout=SLOW)
    return guest, fight_id


def defend_all(guest):
    """Commands every monster that has not chosen to defend: the menu offers them one at a time, and its buttons are off while one is being sent."""
    for _ in range(8):
        try:
            menu(guest, "defend").click(timeout=2000)  # (waits for the button to be enabled; gone or off: nobody is left to ask)
        except PlaywrightTimeout:
            return


def test_a_guest_waits_in_the_queue_and_leaves_it(page, new_page, live_server):
    guest = new_page()
    queued(guest)
    guest.locator("#monsters-leave").click()
    expect(guest.locator("#monsters-join")).to_be_visible()
    guest.reload()
    expect(guest.locator("#monsters-join")).to_be_visible(), "a guest who left stays out after a reload"


def test_a_waiting_guest_is_still_waiting_after_a_reload(new_page):
    guest = new_page()
    queued(guest)
    guest.reload()
    expect(guest.locator("#monsters-place")).to_contain_text("number 1")


def test_a_guest_whose_token_the_server_forgot_is_asked_to_join_again(new_page, console):
    console.allow("status of 401")
    guest = new_page()
    guest.goto("/monsters/")
    guest.evaluate("localStorage.setItem('terraforma.monster-guest', 'not a token')")
    guest.reload()
    expect(guest.locator("#monsters-note")).to_contain_text("lapsed")
    expect(guest.locator("#monsters-join")).to_be_visible()


def test_a_game_that_refuses_guests_says_why_on_the_card(new_page, console):
    console.allow("status of 409")
    guest = new_page()
    guest.route("**/api/monsters/guest/join", lambda route: route.fulfill(status=409, content_type="application/json", body='{"detail": "Log in to play monsters."}'))
    guest.goto("/monsters/")
    guest.locator("#monsters-join").click()
    expect(guest.locator("#monsters-problem")).to_have_text("Log in to play monsters.")
    expect(guest.locator("#monsters-join")).to_be_enabled()


def test_a_seated_guest_commands_the_monsters_and_hears_the_round_play(page, new_page, live_server):
    guest, fight_id = seated(page, live_server, new_page)
    monsters = [each for each in view_of(page, fight_id)["fighters"] if not each["yours"]]
    mine = guest.locator(".fighter[data-yours='true']")
    expect(mine).to_have_count(len(monsters))  # (all the monsters are the guest's to command)
    expect(mine.first.locator(".fighter-name")).to_have_text(monsters[0]["name"])
    for _ in monsters:  # (the menu asks for each monster in turn)
        menu(guest, "defend").click()
    expect(mine.first).to_have_attribute("data-committed", "true")
    command(page, fight_id)  # (the hero's side commits, and the round plays)
    stage = guest.locator("#stage")
    expect(stage).to_have_attribute("data-round", "2", timeout=SLOW)
    settled(guest)
    expect(stage).not_to_have_attribute("data-shown", "0")
    expect(guest.locator("#fight-log li").first).to_be_visible()


def test_a_guest_who_missed_a_round_comes_back_and_commands_again(page, new_page, live_server):
    guest, fight_id = seated(page, live_server, new_page)
    command(page, fight_id)

    async def play_without_the_guest(db):
        record = await db.get(FightRecord, fight_id)
        await live.resolve_round(db, record, live_server.game.rules)

    live_server.in_db(play_without_the_guest)  # (the round's time ran out: the AI takes the monsters)
    guest.reload()
    stage = guest.locator("#stage")
    expect(stage).to_have_attribute("data-absent", "true", timeout=SLOW)
    guest.locator("#fight-back").click()
    expect(stage).to_have_attribute("data-absent", "false", timeout=SLOW)
    expect(guest.locator("#fight-back")).to_be_hidden()


def test_a_guest_leaves_the_finished_fight_and_can_join_again(page, new_page, live_server):
    guest, fight_id = seated(page, live_server, new_page)
    live_server.in_db(lambda db: set_hp(db, fight_id, 1, 1))  # (frail monsters and a strong hero: a hit ends it, if it lands)
    live_server.in_db(lambda db: set_hp(db, fight_id, 0, 500, strength=60))
    guest.reload()
    stage = guest.locator("#stage")
    expect(stage).to_have_attribute("data-menu", "commands", timeout=SLOW)
    for _ in range(30):
        if view_of(page, fight_id)["over"]:
            break
        command(page, fight_id)
        defend_all(guest)
        expect(stage).not_to_have_attribute("data-playing", "true", timeout=SLOW)
        guest.wait_for_timeout(200)
    expect(guest.locator("#fight-leave")).to_be_visible(timeout=SLOW)
    guest.locator("#fight-leave").click()
    expect(guest.locator("#monsters-join")).to_be_visible(timeout=SLOW)
