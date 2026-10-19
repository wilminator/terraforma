"""The round time card on the account page in a real browser: the game's longer-round options, what each costs, choosing one and the play time
(the calls behind them are tests/test_timing.py and tests/test_playtime.py)."""

import math

from playwright.sync_api import expect

from .test_account import confirmed_and_logged_in


def test_the_card_shows_the_usual_round_the_options_and_the_play_time(
    page, live_server
):
    confirmed_and_logged_in(page, live_server)
    seconds = live_server.game.rules.round_seconds
    expect(page.locator("#round-time-current")).to_have_text(
        f"A round now waits {seconds} seconds for you."
    )
    expect(page.locator("#round-time-pick option")).to_have_text(
        [
            "1× the usual length",
            "1.5× the usual length (monsters get 5% more HP)",
            "2× the usual length (monsters get 10% more HP)",
        ]
    )
    expect(page.locator("#round-time-pick")).to_have_value("1")
    expect(page.locator("#play-time")).to_have_text("You have played 0.0 hours.")


def test_a_longer_round_is_chosen_and_kept(page, live_server):
    confirmed_and_logged_in(page, live_server)
    page.locator("#round-time-pick").select_option("1.5")
    page.locator("#round-time-form button[type=submit]").click()
    longer = math.ceil(live_server.game.rules.round_seconds * 1.5)
    expect(page.locator("#round-time-form .note")).to_have_text(
        "Saved. It counts for fights that start from now on."
    )
    expect(page.locator("#round-time-current")).to_have_text(
        f"A round now waits {longer} seconds for you."
    )
    page.reload()  # what is shown is the server's, so the choice is still there
    expect(page.locator("#round-time-pick")).to_have_value("1.5")
    expect(page.locator("#round-time-current")).to_have_text(
        f"A round now waits {longer} seconds for you."
    )
    page.locator("#round-time-pick").select_option("1")
    page.locator("#round-time-form button[type=submit]").click()
    expect(page.locator("#round-time-current")).to_have_text(
        f"A round now waits {live_server.game.rules.round_seconds} seconds for you."
    )
