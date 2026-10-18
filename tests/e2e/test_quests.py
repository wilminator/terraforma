"""The Quests panel in a real browser: the team's quest of the week and the quests the guild slots hold, asking for them and seeing the
server's answer (the calls behind it are tests/test_weekly_quests.py and tests/test_rotating_quests.py)."""

from playwright.sync_api import expect

from terraforma.content.loader import load_content

from .test_heroes import open_game, tab
from .test_pack import Player

SLOT = {
    "key": "rank-s",
    "name": "Rank S promotion",
    "guild": "vanguard",
    "rank": 5,
    "min_days": 10,
    "max_days": 20,
    "grace_days": 3,
    "limit_days": 5,
}
WEEKLY = {
    "key": "cellar",
    "name": "Rats in the cellar",
    "min_level": 1,
    "max_level": 999,
    "weight": 1,
    "spec": {"kill": {"rat": 3}},
}
DRAGON = {
    "key": "dragon",
    "name": "Slay the dragon",
    "slot": "rank-s",
    "weight": 1,
    "spec": {"boss": "dragon"},
}


def start(page, live_server, quests=True):
    """A logged-in player with a team, in the game with the Quests panel open; $quests adds a weekly template and a slot with one quest."""
    open_game(page, live_server)
    if quests:
        live_server.in_db(
            lambda db: load_content(
                db, {"quest_slots": [SLOT], "quest_templates": [WEEKLY, DRAGON]}
            )
        )
    player = Player(page, "Mike")
    player.post(
        "/api/teams",
        {"name": "Vanguard", "heroes": [{"name": "Ayla", "job": "fighter"}]},
    )
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Quests")
    return player


def test_without_a_team_the_panel_says_to_make_one(page, live_server):
    open_game(page, live_server)
    tab(page, "Quests")
    expect(page.locator("#quests-none")).to_have_text("Make a team to see its quests.")


def test_a_team_asks_for_its_quest_of_the_week(page, live_server):
    start(page, live_server)
    expect(page.locator("#quests-weekly-none")).to_be_visible()
    page.locator("#quests-weekly-ask").click()
    expect(page.locator("#quests-weekly-name")).to_have_text("Rats in the cellar")
    expect(page.locator("#quests-weekly-status")).to_contain_text("Offered")
    expect(page.locator("#quests-note")).to_have_text("Asked for this week's quest.")
    page.reload()  # what is shown is the server's, so it is there again
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Quests")
    expect(page.locator("#quests-weekly-name")).to_have_text("Rats in the cellar")


def test_a_team_without_any_template_is_told_the_game_has_none(page, live_server):
    start(page, live_server, quests=False)
    page.locator("#quests-weekly-ask").click()
    expect(page.locator("#quests-weekly-none")).to_be_visible()
    expect(page.locator("#quests-weekly-name")).to_have_count(0)
    expect(page.locator("#quests-slots-none")).to_be_visible()


def test_a_slot_quest_shows_once_asked_and_is_then_the_teams(page, live_server):
    player = start(page, live_server)
    team_id = player.get("/api/teams")[0]["id"]
    expect(
        page.locator("#quests-slots-none")
    ).to_be_visible()  # nobody has asked about the slot this period
    assert (
        player.post(f"/api/teams/{team_id}/slot-quests/rank-s/offer", {})["slot"][
            "quest"
        ]["name"]
        == "Slay the dragon"
    )
    page.get_by_role("button", name="Refresh", exact=True).click()
    row = page.locator('#quests-slots [data-slot="rank-s"]')
    expect(row).to_contain_text("Rank S promotion")
    expect(row).to_contain_text("Slay the dragon")
    expect(row.locator("[data-team-status]")).to_have_text("Offered")
    expect(row.get_by_role("button")).to_have_count(0)  # the team already holds it
