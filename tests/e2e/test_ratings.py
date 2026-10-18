"""The Ratings panel in a real browser: the questions asked after a fight, answered with a score, by accepting the game's suggestion, or
dismissed (the calls behind it are tests/test_ratings.py). The questions are put to the player's team in the database, as the end of a fight would."""

from playwright.sync_api import expect

from terraforma.relations.models import RatingPrompt

from .test_fight_pages import a_finished_fight
from .test_heroes import open_game, tab
from .test_pack import Player


def start(page, live_server, questions=((None, ""),)):
    """Mike's team Alpha (Aria) and the team Beta (Bryn) of another player, with one question per $questions item ($suggested change, reason):
    Alpha is asked about Beta. The Ratings panel is open."""
    open_game(page, live_server)
    mike = Player(page, "Mike")
    mike.post(
        "/api/teams", {"name": "Alpha", "heroes": [{"name": "Aria", "job": "fighter"}]}
    )
    teams = mike.get("/api/teams")
    mike.post(
        "/api/teams", {"name": "Beta", "heroes": [{"name": "Bryn", "job": "fighter"}]}
    )
    alpha, beta = teams[0]["id"], mike.get("/api/teams")[1]["id"]
    fight_id, _guid = a_finished_fight(live_server, mike.heroes()["Aria"], public=False)

    async def work(db):
        for suggested, reason in questions:
            db.add(
                RatingPrompt(
                    fight_id=fight_id,
                    subject_team_id=alpha,
                    object_team_id=beta,
                    interaction="harmed" if suggested is None else "asked",
                    suggested=suggested,
                    reason=reason,
                )
            )

    live_server.in_db(work)
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Ratings")


def test_with_no_questions_the_panel_says_nobody_is_waiting(page, live_server):
    open_game(page, live_server)
    tab(page, "Ratings")
    expect(page.locator("#ratings-none")).to_have_text(
        "Nobody is waiting for your opinion."
    )
    expect(page.locator("#ratings-scale")).to_contain_text("neutral (-20 to 20)")


def test_a_question_is_answered_with_a_score(page, live_server):
    start(page, live_server)
    row = page.locator("#ratings-list li")
    expect(row).to_contain_text("Beta harmed Alpha in a fight")
    page.get_by_role("button", name="Rate Beta").click()
    expect(page.locator("#ratings-problem")).to_have_text("Type a score first.")
    page.get_by_label("Your score for Beta").fill("-80")
    page.get_by_role("button", name="Rate Beta").click()
    expect(page.locator("#ratings-note")).to_have_text("Beta is now enemy (-80).")
    expect(page.locator("#ratings-none")).to_be_visible()  # the question is closed
    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    tab(page, "Ratings")
    expect(page.locator("#ratings-none")).to_be_visible()


def test_a_question_the_game_asked_can_take_its_suggestion(page, live_server):
    start(page, live_server, [(-15, "you nuked a neutral team")])
    expect(page.locator("#ratings-list li")).to_contain_text("you nuked a neutral team")
    page.get_by_role("button", name="Accept the suggestion for Beta").click()
    expect(page.locator("#ratings-note")).to_contain_text("Beta is now")
    expect(page.locator("#ratings-none")).to_be_visible()


def test_a_question_can_be_dismissed_without_rating(page, live_server):
    start(page, live_server)
    expect(
        page.get_by_role("button", name="Accept the suggestion for Beta")
    ).to_have_count(0)  # (the game suggested nothing)
    page.get_by_role("button", name="Dismiss the question about Beta").click()
    expect(page.locator("#ratings-note")).to_have_text("Question dismissed.")
    expect(page.locator("#ratings-none")).to_be_visible()
