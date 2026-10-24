"""How a team sees others, on the team's own page in a real browser: a score on the game's scale and a private note for another player's
team, then forgetting it (the calls behind it are tests/test_relations.py)."""

from playwright.sync_api import expect

from .test_alliances import make_team, second_player
from .test_heroes import open_game
from .test_pack import Player


def start(page, new_page, live_server):
    """Mike's team Alpha, another player's team Beta, and Alpha's page open; returns Beta's number."""
    open_game(page, live_server)

    alpha = Player(page, "Mike").post(
        "/api/teams", {"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]}
    )
    _page, other = second_player(new_page, live_server, "Zed")
    beta = make_team(other, "Beta")
    page.goto(f"/team/{alpha['id']}")
    return beta["id"]


def test_a_team_with_no_views_says_so(page, new_page, live_server):
    start(page, new_page, live_server)
    expect(page.locator("#relationships")).to_contain_text(
        "Your team has no views of anyone yet."
    )


def test_a_team_sets_a_score_and_a_note_for_another_team_and_forgets_it(
    page, new_page, live_server
):
    beta = start(page, new_page, live_server)
    form = page.locator("#relation-form")
    form.get_by_label("Number").fill(str(beta))
    form.get_by_label("Score").fill("-80")
    form.get_by_label("Private note").fill("Raided our camp")
    form.get_by_role("button", name="Save view").click()
    row = page.locator("[data-relationship='Beta']")
    expect(row).to_contain_text("enemy, -80")
    expect(row).to_contain_text("Raided our camp")
    page.reload()  # what is shown is the server's, so it is still there
    expect(page.locator("[data-relationship='Beta']")).to_contain_text("enemy, -80")
    page.get_by_role("button", name="Forget Beta").click()
    expect(page.locator("[data-relationship='Beta']")).to_have_count(0)
    expect(page.locator("#relationships")).to_contain_text(
        "Your team has no views of anyone yet."
    )
