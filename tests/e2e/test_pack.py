"""The Pack panel in a real browser: a hero's gold and items, giving them to a teammate and the ledger of what was given and received
(the calls behind it are tests/test_trading.py). The game here keeps gold on the hero, so a gift of gold has somewhere to go."""

import dataclasses

import pytest
from playwright.sync_api import expect
from sqlalchemy import update

from terraforma.economy import HeroGold
from terraforma.example import GAME
from terraforma.heroes import inventory
from terraforma.heroes.models import Hero
from terraforma.keys import new_key

from .conftest import LiveServer
from .test_heroes import open_game, tab
from .test_profile import Calls


@pytest.fixture
def live_server(database_url, mailbox, tmp_path):
    """The example game with the gold on each hero (its own economy keeps it on the team, where a gift between teammates has nowhere to go)."""
    new_key(tmp_path / "keys")
    server = LiveServer(database_url, tmp_path / "keys", mailbox, game=dataclasses.replace(GAME, economy=HeroGold())).start()
    yield server
    server.stop()


class Player(Calls):
    def get(self, path):
        answer = self.page.request.get(path)
        assert answer.ok, answer.text()
        return answer.json()

    def heroes(self):
        return {hero["name"]: hero["id"] for hero in self.get("/api/heroes")}


def start(page, live_server, teams):
    """A logged-in player with $teams ({name: [hero names]}), in the game with the Pack panel open."""
    open_game(page, live_server)
    player = Player(page, "Mike")
    for name, heroes in teams.items():
        player.post("/api/teams", {"name": name, "heroes": [{"name": hero, "job": "fighter"} for hero in heroes]})
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Pack")
    return player


def fill(live_server, hero_id, gold=0, items=()):
    async def work(db):
        if gold:
            await db.execute(update(Hero).where(Hero.id == hero_id).values(gold=gold))
        hero = await db.get(Hero, hero_id)
        for key, qty in items:
            assert await inventory.add_item(db, hero, key, qty) == 0

    live_server.in_db(work)


def test_the_pack_says_when_there_are_no_heroes(page, live_server):
    open_game(page, live_server)
    tab(page, "Pack")
    expect(page.locator("#pack-none")).to_have_text("You have no heroes yet. Make a team and add one.")


def test_a_hero_gives_gold_to_a_teammate_and_both_ledgers_show_it(page, live_server):
    player = start(page, live_server, {"Alpha": ["Aria", "Bram"]})
    heroes = player.heroes()
    fill(live_server, heroes["Aria"], gold=50)
    tab(page, "Pack")
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("#pack-hero")).to_have_value(str(heroes["Aria"]))
    expect(page.locator("#pack-gold-total")).to_have_text("Gold: 50")
    expect(page.locator("#pack-ledger-none")).to_have_text("Nothing given or received yet.")

    page.locator("#pack-to").select_option(label="Bram (Alpha)")
    page.locator("#pack-gold-amount").fill("20")
    page.locator("#pack-give-gold").click()
    expect(page.locator("#pack-note")).to_have_text("Gave 20 gold to Bram.")
    expect(page.locator("#pack-gold-total")).to_have_text("Gold: 30")
    expect(page.locator("#pack-ledger")).to_have_text("Gave 20 gold to Bram")

    page.locator("#pack-hero").select_option(label="Bram")
    expect(page.locator("#pack-gold-total")).to_have_text("Gold: 20")
    expect(page.locator("#pack-ledger")).to_have_text("Received 20 gold from Aria")


def test_a_hero_gives_part_of_a_stack_of_items_to_a_teammate(page, live_server):
    player = start(page, live_server, {"Alpha": ["Aria", "Bram"]})
    heroes = player.heroes()
    fill(live_server, heroes["Aria"], items=[("potion", 3)])
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("[data-item='Potion']")).to_contain_text("×3")

    page.locator("#pack-to").select_option(label="Bram (Alpha)")
    page.get_by_label("How many Potion to give").fill("2")
    page.get_by_role("button", name="Give Potion").click()
    expect(page.locator("#pack-note")).to_have_text("Gave 2 × Potion to Bram.")
    expect(page.locator("[data-item='Potion']")).to_contain_text("×1")
    expect(page.locator("#pack-ledger")).to_have_text("Gave 2 × Potion to Bram")

    page.locator("#pack-hero").select_option(label="Bram")
    expect(page.locator("[data-item='Potion']")).to_contain_text("×2")
    expect(page.locator("#pack-ledger")).to_have_text("Received 2 × Potion from Aria")


def test_a_gift_the_game_refuses_says_why_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    console.allow("status of 404")
    player = start(page, live_server, {"Alpha": ["Aria", "Bram"], "Beta": ["Cole"]})
    heroes = player.heroes()
    fill(live_server, heroes["Aria"], gold=5)
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("#pack-gold-total")).to_have_text("Gold: 5")

    page.locator("#pack-gold-amount").fill("5")
    page.locator("#pack-give-gold").click()
    expect(page.locator("#pack-problem")).to_have_text("Choose who to give to.")  # (nothing was sent)

    page.locator("#pack-to").select_option(label="Bram (Alpha)")
    page.locator("#pack-gold-amount").fill("6")
    page.locator("#pack-give-gold").click()
    expect(page.locator("#pack-problem")).to_have_text("not enough gold")
    expect(page.locator("#pack-gold-total")).to_have_text("Gold: 5")

    page.locator("#pack-to").select_option(label="Cole (Beta)")  # another team: the game's rule is teammates only
    page.locator("#pack-gold-amount").fill("1")
    page.locator("#pack-give-gold").click()
    expect(page.locator("#pack-problem")).to_have_text("you can't trade with that hero")

    page.locator("#pack-to").select_option(label="Choose a hero")  # a number typed in is a gift to that hero as well
    page.locator("#pack-to-number").fill(str(heroes["Cole"]))
    page.locator("#pack-give-gold").click()
    expect(page.locator("#pack-problem")).to_have_text("you can't trade with that hero")
    expect(page.locator("#pack-ledger-none")).to_be_visible()


def test_a_hero_chosen_in_the_pack_is_the_hero_nearby_acts_with(page, live_server):
    start(page, live_server, {"Alpha": ["Aria", "Bram"]})
    page.locator("#pack-hero").select_option(label="Bram")
    expect(page.locator("#pack-hero")).to_have_value(str(Player(page, "Mike").heroes()["Bram"]))
    tab(page, "Nearby")
    expect(page.locator("#nearby-hero")).to_contain_text("Bram")
