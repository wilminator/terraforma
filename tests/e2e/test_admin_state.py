"""The Settings section of the admin page in a real browser: the live snapshot and the draft, editing a section, the check, committing, reverting and
the history (the calls behind it are tests/test_game_state.py). The game here has one section of its own, ``rates``."""

import dataclasses
import re

import pytest
from playwright.sync_api import expect

from terraforma.example import GAME
from terraforma.gamestate.document import Section
from terraforma.keys import new_key

from .conftest import LiveServer
from .test_admin import admin_page, tab


class Rates(Section):
    name = "rates"

    def default(self):
        return {"xp": 1.0}

    def check(self, value, document):
        return (
            []
            if isinstance(value.get("xp"), int | float) and value["xp"] >= 0
            else ["xp must be a number, 0 or more"]
        )


@pytest.fixture
def live_server(database_url, mailbox, tmp_path):
    """The example game with a section of its own in the settings document."""
    new_key(tmp_path / "keys")
    server = LiveServer(
        database_url,
        tmp_path / "keys",
        mailbox,
        game=dataclasses.replace(GAME, state_sections=(Rates(),)),
    ).start()
    yield server
    server.stop()


def open_settings(page, live_server):
    admin_page(page, live_server)
    tab(page, "Settings")
    expect(page.locator("#admin-state")).to_be_visible()
    expect(page.locator("#state-live")).to_contain_text("Live: snapshot")
    return live_id(page)


def live_id(page):
    """The number of the snapshot that is live (the engine makes the first state itself, so the test does not assume which number it has)."""
    return int(
        re.search(r"snapshot (\d+)", page.locator("#state-live").inner_text()).group(1)
    )


def edit(page, text):
    page.locator("#state-section").select_option("rates")
    page.locator("#state-value").fill(text)
    page.locator("#state-save").click()


def test_the_page_shows_the_live_snapshot_the_draft_and_the_sections(page, live_server):
    open_settings(page, live_server)
    expect(page.locator("#state-draft")).to_contain_text("the same as live")
    expect(page.locator("#state-section option")).to_have_text(["components", "rates"])
    page.locator("#state-section").select_option("rates")
    expect(page.locator("#state-value")).to_have_value('{\n  "xp": 1\n}')
    expect(page.locator("#state-export-planned")).to_have_attribute(
        "href", "/api/admin/state/export"
    )


def test_a_section_is_edited_checked_and_committed_to_live(page, live_server):
    first = open_settings(page, live_server)
    edit(page, '{"xp": 2}')
    expect(page.locator("#state-note")).to_have_text("Saved rates in the draft.")
    expect(page.locator("#state-draft")).to_contain_text("different from live")
    page.locator("#state-check").click()
    expect(page.locator("#state-note")).to_have_text("The draft checks out.")
    page.get_by_label("Note for the snapshot (optional)").fill("Doubled xp")
    page.locator("#state-commit").click()
    expect(page.locator("#state-note")).to_have_text("Committed: the draft is live.")
    expect(page.locator("#state-live")).to_contain_text("Doubled xp")
    assert live_id(page) > first
    expect(page.locator("#state-draft")).to_contain_text("the same as live")
    expect(
        page.locator(f"#state-history [data-snapshot='{live_id(page)}']")
    ).to_contain_text("Doubled xp")
    expect(
        page.locator(f"#state-history [data-snapshot='{first}']")
    ).to_be_visible()  # (the older one is still in the history)


def test_text_that_is_not_json_is_refused_here_and_a_bad_value_is_refused_by_the_check(
    page, live_server
):
    first = open_settings(page, live_server)
    edit(page, "{xp:")
    expect(page.locator("#state-problem")).to_have_text("That is not valid JSON.")
    edit(page, '{"xp": -5}')
    expect(page.locator("#state-note")).to_have_text("Saved rates in the draft.")
    page.locator("#state-commit").click()
    expect(page.locator("#state-found")).to_contain_text(
        "rates: xp must be a number, 0 or more"
    )
    expect(page.locator("#state-note")).to_have_text("The draft has problems.")
    assert live_id(page) == first  # (nothing was committed)


def test_dropping_the_edits_makes_the_draft_the_live_state_again(page, live_server):
    open_settings(page, live_server)
    edit(page, '{"xp": 3}')
    expect(page.locator("#state-draft")).to_contain_text("different from live")
    page.locator("#state-revert").click()
    page.get_by_role("button", name="Really drop them?").click()
    expect(page.locator("#state-note")).to_have_text(
        "The draft is the live state again."
    )
    expect(page.locator("#state-draft")).to_contain_text("the same as live")
    expect(page.locator("#state-value")).to_have_value('{\n  "xp": 1\n}')
