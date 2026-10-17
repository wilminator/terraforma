"""The Storage part of the Pack panel in a real browser: a game that keeps storage shows it, items go in and come out, and the game's
refusals are shown in its own words (the calls behind it are tests/test_storage.py; a game with none is in test_pack.py)."""

import dataclasses

import pytest
from playwright.sync_api import expect

from terraforma.economy import HeroGold
from terraforma.example import GAME
from terraforma.keys import new_key
from terraforma.storage.hooks import TeamStorage

from .conftest import LiveServer
from .test_heroes import open_game, tab
from .test_pack import Player, fill


class SmallStorage(TeamStorage):
    capacity = 1


@pytest.fixture
def live_server(database_url, mailbox, tmp_path):
    """The example game with a team's storage of one stack."""
    new_key(tmp_path / "keys")
    game = dataclasses.replace(GAME, economy=HeroGold(), storage=SmallStorage())
    server = LiveServer(database_url, tmp_path / "keys", mailbox, game=game).start()
    yield server
    server.stop()


def start(page, live_server):
    """A logged-in player with one hero holding three potions, in the game with the Pack panel open."""
    open_game(page, live_server)
    player = Player(page, "Mike")
    player.post("/api/teams", {"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]})
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Pack")
    fill(live_server, player.heroes()["Aria"], items=[("potion", 3), ("dagger", 1)])
    page.get_by_role("button", name="Refresh").click()
    expect(page.locator("[data-item='Potion']")).to_contain_text("×3")


def test_items_go_into_storage_and_come_out_again(page, live_server):
    start(page, live_server)
    expect(page.locator("#pack-storage-none")).to_have_text("Nothing in storage.")
    expect(page.locator("#pack-storage-count")).to_have_text("Your team's storage: 0 of 1 stacks used.")

    page.get_by_label("How many Potion to give").fill("2")  # (the quantity box is shared by the row's buttons)
    page.get_by_role("button", name="Store Potion").click()
    expect(page.locator("#pack-note")).to_have_text("Stored 2 × Potion.")
    expect(page.locator("[data-item='Potion']")).to_contain_text("×1")
    expect(page.locator("[data-stored='Potion']")).to_contain_text("×2")
    expect(page.locator("#pack-storage-count")).to_have_text("Your team's storage: 1 of 1 stacks used.")

    page.get_by_label("How many Potion to take out").fill("1")
    page.get_by_role("button", name="Take Potion out").click()
    expect(page.locator("#pack-note")).to_have_text("Took 1 × Potion out of storage.")
    expect(page.locator("[data-item='Potion']")).to_contain_text("×2")
    expect(page.locator("[data-stored='Potion']")).to_contain_text("×1")


def test_what_the_game_refuses_is_shown_in_its_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    start(page, live_server)
    page.get_by_role("button", name="Store Potion").click()
    expect(page.locator("[data-stored='Potion']")).to_contain_text("×1")

    page.get_by_role("button", name="Store Dagger").click()  # (one stack only)
    expect(page.locator("#pack-problem")).to_have_text("storage has no room for that")
    expect(page.locator("[data-item='Dagger']")).to_have_count(1)

    page.get_by_role("button", name="Use Potion from storage").click()  # (the default rule: take it out first)
    expect(page.locator("#pack-problem")).to_have_text("take it out of storage first")
