"""The profile page and the public pages in a real browser: a player writes a bio, joins the directory, shows a team and shares the
links; anyone with a link reads the page without a login (the calls behind them are tests/test_profiles.py)."""

import re

from playwright.sync_api import expect

from terraforma.accounts.service import set_handle
from terraforma.models import Account

from .conftest import PASSWORD
from .test_smoke import log_in


class Calls:
    """The server's calls as a logged-in player makes them, for what a test sets up before it opens a page."""

    def __init__(self, page, username):
        self.page = page
        answer = page.request.post("/api/login", data={"username": username, "password": PASSWORD})
        assert answer.ok, answer.text()
        self.csrf = {"X-CSRF-Token": answer.json()["csrf_token"]}

    def post(self, path, body):
        answer = self.page.request.post(path, data=body, headers=self.csrf)
        assert answer.ok, answer.text()
        return answer.json()


def player_with_handle(live_server, username, handle):
    account = live_server.make_account(username)

    async def work(db):
        await set_handle(db, await db.get(Account, account.id), handle)

    live_server.in_db(work)


def test_the_profile_page_sends_a_logged_out_player_to_the_login_and_back(page, live_server):
    live_server.make_account("Mike")
    page.goto("/profile/")
    expect(page).to_have_url("/account/?next=profile")
    log_in(page, "Mike")
    expect(page).to_have_url("/profile/")
    expect(page.get_by_role("heading", name="Your page", exact=True)).to_be_visible()


def test_a_player_makes_a_page_writes_a_bio_and_shows_a_team(page, live_server):
    player_with_handle(live_server, "Mike", "MikeTheBold")
    page.goto("/account/")
    log_in(page, "Mike")
    calls = Calls(page, "Mike")
    calls.post("/api/teams", {"name": "Vanguard", "heroes": [{"name": "Aria", "job": "fighter"}]})
    page.get_by_role("link", name="Your page").click()
    expect(page).to_have_url("/profile/")

    expect(page.locator("#profile-state")).to_have_text("You have no page yet.")
    page.get_by_role("button", name="Make my page").click()
    address = page.locator("#profile-address")
    expect(address).to_contain_text("/p/")
    expect(page.locator("#profile-state")).to_have_text("Your page is public to anyone with its address.")

    page.locator("#profile-bio").fill("Hello, travellers.")
    page.get_by_role("button", name="Save bio").click()
    expect(page.locator("#bio-note")).to_have_text("Bio saved.")

    team = page.locator("[data-team='Vanguard']")
    expect(team).to_contain_text("Hidden")
    team.get_by_label("Show Vanguard on my page").check()
    expect(team).to_contain_text("Shown")
    expect(team.get_by_role("link", name="Page of Vanguard")).to_be_visible()

    page.reload()
    expect(page.locator("#profile-bio")).to_have_value("Hello, travellers.")
    expect(page.locator("[data-team='Vanguard']").get_by_label("Show Vanguard on my page")).to_be_checked()


def test_the_directory_choice_and_a_new_address_are_kept(page, live_server):
    player_with_handle(live_server, "Mike", "MikeTheBold")
    page.goto("/profile/")
    log_in(page, "Mike")
    page.get_by_role("button", name="Make my page").click()
    first = page.locator("#profile-address a").get_attribute("href")

    box = page.get_by_label("List my handle in the directory")
    expect(box).not_to_be_checked()
    box.check()
    expect(page.locator("#directory-note")).to_have_text("Your handle is listed.")
    page.reload()
    expect(page.get_by_label("List my handle in the directory")).to_be_checked()

    page.get_by_role("button", name="New address").first.click()
    expect(page.locator("#profile-note")).to_have_text("Your page has a new address. The old one no longer works.")
    assert page.locator("#profile-address a").get_attribute("href") != first


def test_anyone_with_the_link_reads_the_page_and_follows_it_to_the_team_and_the_alliance(page, new_page, live_server):
    player_with_handle(live_server, "Mike", "MikeTheBold")
    page.goto("/account/")
    log_in(page, "Mike")
    calls = Calls(page, "Mike")
    team = calls.post("/api/teams", {"name": "Vanguard", "heroes": [{"name": "Aria", "job": "fighter"}]})
    calls.post("/api/teams", {"name": "Hidden Ones", "heroes": [{"name": "Bo", "job": "fighter"}]})
    alliance = calls.post("/api/alliances", {"team_id": team["id"], "name": "Iron Pact"})
    page.request.put("/api/profile/bio", data={"bio": "Hello, travellers."}, headers=calls.csrf)
    page.request.put("/api/profile/team-visible", data={"team_id": team["id"], "visible": True}, headers=calls.csrf)
    page.request.post(f"/api/alliances/{alliance['id']}/profile/token", data={}, headers=calls.csrf)
    page.request.put(f"/api/alliances/{alliance['id']}/profile/bio", data={"bio": "We hold the pass."}, headers=calls.csrf)
    mine = page.request.get("/api/profile").json()

    visitor = new_page()  # another browser, never logged in
    visitor.goto(f"/p/{mine['token']}")
    expect(visitor.get_by_role("heading", name="MikeTheBold")).to_be_visible()
    expect(visitor.get_by_text("Hello, travellers.")).to_be_visible()
    expect(visitor.get_by_role("link", name="Vanguard")).to_be_visible()
    expect(visitor.get_by_text("Hidden Ones")).to_have_count(0)
    expect(visitor).to_have_title(re.compile(r"^MikeTheBold · "))

    visitor.get_by_role("link", name="Vanguard").click()
    expect(visitor).to_have_url(re.compile(r"/p/team/"))
    expect(visitor.get_by_role("heading", name="Vanguard")).to_be_visible()
    visitor.get_by_role("link", name="Iron Pact").click()
    expect(visitor).to_have_url(re.compile(r"/p/alliance/"))
    expect(visitor.get_by_role("heading", name="Iron Pact")).to_be_visible()
    expect(visitor.get_by_text("We hold the pass.")).to_be_visible()
    expect(visitor.locator("[data-team='Vanguard']")).to_contain_text("leader")


def test_an_address_that_goes_nowhere_says_so(page, console):
    console.allow("status of 404")  # the browser logs the refusal itself
    page.goto("/p/doesnotexist1234")
    expect(page.get_by_role("heading", name="No such page")).to_be_visible()
    page.goto("/p/team/doesnotexist1234")
    expect(page.get_by_role("heading", name="No such page")).to_be_visible()
