"""The alliance screens in a real browser: found one, invite and answer, run the roster, speak for it with a page, ballots and
relationships (the calls behind them are tests/test_alliances.py, tests/test_ballots.py and tests/test_profiles.py)."""

import re

from playwright.sync_api import expect

from .test_profile import Calls, player_with_handle
from .test_smoke import log_in



def make_team(calls, name):
    """A team of one fighter; a player's hero names are unique, so the hero is named for the team."""
    return calls.post("/api/teams", {"name": name, "heroes": [{"name": f"{name} hero", "job": "fighter"}]})


def sign_in(page, username):
    """Logs in on the account page and waits for it to finish, so the calls a test then makes don't race the login."""
    page.goto("/account/")
    log_in(page, username)
    expect(page.locator("#who")).to_be_visible()
    return Calls(page, username)


def second_player(new_page, live_server, username):
    """Another browser with its own login, and the calls it makes for setting things up."""
    live_server.make_account(username)
    page = new_page()
    return page, sign_in(page, username)


def open_alliances(page):
    page.goto("/alliances/")
    return page


def test_the_alliance_pages_send_a_logged_out_player_to_the_login_and_back(page, live_server):
    live_server.make_account("Mike")
    page.goto("/alliances/")
    expect(page).to_have_url("/account/?next=alliances")
    log_in(page, "Mike")
    expect(page).to_have_url("/alliances/")
    expect(page.get_by_role("heading", name="Your alliances", exact=True)).to_be_visible()


def test_the_account_page_links_to_the_alliances(page, live_server):
    live_server.make_account("Mike")
    page.goto("/account/")
    log_in(page, "Mike")
    page.get_by_role("link", name="Your alliances").click()
    expect(page).to_have_url("/alliances/")


def test_a_player_with_no_team_is_told_to_make_one_first(page, live_server):
    live_server.make_account("Mike")
    page.goto("/alliances/")
    log_in(page, "Mike")
    expect(page.get_by_text("You have no alliances yet.")).to_be_visible()
    expect(page.get_by_text("An alliance is founded by a team. Make a team first.")).to_be_visible()
    expect(page.locator("#found-form")).to_have_count(0)


def test_a_player_founds_an_alliance_and_lands_on_its_page(page, live_server):
    live_server.make_account("Mike")
    make_team(sign_in(page, "Mike"), "Vanguard")
    page.goto("/alliances/")
    form = page.locator("#found-form")
    form.get_by_label("Alliance name").fill("Iron Pact")
    form.get_by_role("button", name="Found alliance").click()
    expect(page).to_have_url(re.compile(r"/alliance/\d+"))
    expect(page.get_by_role("heading", name="Iron Pact", exact=True)).to_be_visible()
    expect(page.locator("#you")).to_have_text("You act as Vanguard, role: commander.")
    expect(page.locator("[data-team='Vanguard']")).to_contain_text("commander")

    page.get_by_role("link", name="Your alliances").click()
    expect(page.locator("[data-alliance='Iron Pact']")).to_contain_text("Vanguard")
    page.locator("[data-alliance='Iron Pact']").get_by_role("link", name="Iron Pact").click()
    expect(page.get_by_role("heading", name="Iron Pact", exact=True)).to_be_visible()


def test_a_blank_alliance_name_is_refused_in_the_servers_words(page, live_server, console):
    console.allow("status of")  # the browser logs the refusal itself
    live_server.make_account("Mike")
    make_team(sign_in(page, "Mike"), "Vanguard")
    page.goto("/alliances/")
    page.locator("#found-form").get_by_label("Alliance name").fill("   ")
    page.locator("#found-form").get_by_role("button", name="Found alliance").click()
    expect(page.locator("#found-form .problem")).not_to_be_empty()
    expect(page).to_have_url("/alliances/")


def test_an_invited_team_accepts_or_declines_and_the_inviter_can_withdraw(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    strangers = make_team(zed, "Strangers")
    wanderers = make_team(zed, "Wanderers")

    page.goto(f"/alliance/{alliance['id']}")
    invite = page.locator("#invite-form")
    invite.get_by_label("Team number to invite").fill(str(strangers["id"]))
    invite.get_by_role("button", name="Invite").click()
    expect(page.locator("[data-invited='Strangers']")).to_be_visible()
    invite.get_by_label("Team number to invite").fill(str(wanderers["id"]))
    invite.get_by_role("button", name="Invite").click()
    expect(page.locator("[data-invited='Wanderers']")).to_be_visible()

    zed_page.goto("/alliances/")
    expect(zed_page.locator("[data-invitation='Iron Pact'][data-team='Strangers']")).to_be_visible()
    zed_page.locator("[data-invitation='Iron Pact'][data-team='Wanderers']").get_by_role("button", name="Decline").click()
    expect(zed_page.locator("[data-invitation='Iron Pact'][data-team='Wanderers']")).to_have_count(0)
    zed_page.locator("[data-invitation='Iron Pact']").get_by_role("button", name="Accept").click()
    expect(zed_page).to_have_url(re.compile(r"/alliance/\d+"))
    expect(zed_page.locator("#you")).to_have_text("You act as Strangers, role: member.")
    expect(zed_page.locator("[data-team='Vanguard']")).to_contain_text("commander")
    expect(zed_page.locator("#invite-form")).to_have_count(0)  # a member's role may not invite

    page.reload()
    expect(page.locator("[data-team='Strangers']")).to_contain_text("member")
    expect(page.locator("[data-invited='Wanderers']")).to_have_count(0)  # declined: gone from the pending list too

    third = make_team(zed, "Drifters")  # a third invitation, withdrawn before it is answered
    page.locator("#invite-form").get_by_label("Team number to invite").fill(str(third["id"]))
    page.locator("#invite-form").get_by_role("button", name="Invite").click()
    page.locator("[data-invited='Drifters']").get_by_role("button", name="Withdraw").click()
    expect(page.locator("[data-invited='Drifters']")).to_have_count(0)


def test_the_roster_links_only_visible_teams_and_names_players_only_in_the_directory(page, new_page, live_server):
    player_with_handle(live_server, "Mike", "MikeTheBold")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    strangers = make_team(zed, "Strangers")
    mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": strangers["id"]})
    zed.post(f"/api/teams/{strangers['id']}/invitations/accept", {"alliance_id": alliance["id"]})

    zed_page.goto(f"/alliance/{alliance['id']}")
    row = zed_page.locator("[data-team='Vanguard']")
    expect(row).to_be_visible()
    expect(row.get_by_role("link")).to_have_count(0)  # Vanguard is hidden: its name alone
    expect(zed_page.get_by_text("MikeTheBold")).to_have_count(0)

    page.request.put("/api/profile/team-visible", data={"team_id": vanguard["id"], "visible": True}, headers=mike.csrf)
    zed_page.reload()
    expect(row.get_by_role("link", name="Vanguard")).to_have_attribute("href", re.compile(r"^/p/team/"))
    expect(zed_page.get_by_text("MikeTheBold")).to_have_count(0)  # visible, but its player is not in the directory

    page.request.put("/api/profile/directory", data={"enabled": True}, headers=mike.csrf)
    zed_page.reload()
    expect(row.get_by_role("link", name="MikeTheBold")).to_have_attribute("href", re.compile(r"^/p/"))


def test_a_slow_answer_does_not_draw_the_page_over_a_newer_one(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    for name in ("Strangers", "Wanderers"):
        team = make_team(zed, name)
        mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": team["id"]})
        zed.post(f"/api/teams/{team['id']}/invitations/accept", {"alliance_id": alliance["id"]})
    page.goto(f"/alliance/{alliance['id']}")
    expect(page.locator("[data-team='Wanderers']")).to_be_visible()
    held = []

    def hold_the_first(route):  # the roster asked for after the role change is read now, but arrives late
        if held:
            route.continue_()
        else:
            held.append((route, route.fetch()))

    page.route("**/roster", hold_the_first)
    page.locator("[data-team='Strangers']").get_by_label("Role of Strangers").select_option("subcommander")
    while not held:
        page.wait_for_timeout(20)
    page.locator("[data-team='Wanderers']").get_by_role("button", name="Remove Wanderers").click()
    expect(page.locator("[data-team='Wanderers']")).to_have_count(0)
    route, answer = held[0]
    route.fulfill(response=answer)  # the late answer still lists Wanderers
    page.wait_for_timeout(500)
    expect(page.locator("[data-team='Wanderers']")).to_have_count(0)


def test_a_commander_finds_a_team_by_name_and_invites_it_without_its_number(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    make_team(zed, "Strangers")
    wanderers = make_team(zed, "Wanderers")
    zed_page.request.put("/api/profile/team-visible", data={"team_id": wanderers["id"], "visible": True}, headers=zed.csrf)

    page.goto(f"/alliance/{alliance['id']}")
    search = page.locator("#team-search-form")
    search.get_by_label("Start of the team's name").fill("str")
    search.get_by_role("button", name="Search").click()
    expect(search.get_by_text("No team by that name.")).to_be_visible()  # Strangers is hidden: found nowhere
    expect(page.locator("[data-found]")).to_have_count(0)

    search.get_by_label("Start of the team's name").fill("wan")
    search.get_by_role("button", name="Search").click()
    expect(page.locator("[data-found='Wanderers']")).to_be_visible()
    page.get_by_role("button", name="Invite Wanderers").click()
    expect(page.locator("[data-invited='Wanderers']")).to_be_visible()

    search = page.locator("#team-search-form")
    search.get_by_label("Start of the team's name").fill("van")
    search.get_by_role("button", name="Search").click()
    expect(page.locator("[data-found='Vanguard']")).to_contain_text("yours")  # one's own teams are always found


def test_a_commander_sets_roles_hands_over_removes_and_the_others_leave(page, new_page, live_server, console):
    console.allow("status of 409")  # the commander can't leave while others remain
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    for name in ("Strangers", "Wanderers"):
        team = make_team(zed, name)
        mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": team["id"]})
        zed.post(f"/api/teams/{team['id']}/invitations/accept", {"alliance_id": alliance["id"]})

    page.goto(f"/alliance/{alliance['id']}")
    strangers = page.locator("[data-team='Strangers']")
    strangers.get_by_label("Role of Strangers").select_option("subcommander")
    expect(strangers.get_by_label("Role of Strangers")).to_have_value("subcommander")
    expect(page.locator("[data-team='Vanguard']").get_by_label("Role of Vanguard")).to_have_count(0)  # not on yourself

    page.locator("[data-team='Wanderers']").get_by_role("button", name="Remove Wanderers").click()
    expect(page.locator("[data-team='Wanderers']")).to_have_count(0)

    page.get_by_role("button", name="Leave alliance").click()  # the commander can't leave while others remain: the server says so
    page.get_by_role("button", name="Really leave").click()
    expect(page.locator("#alliance-problem")).not_to_be_empty()

    strangers.get_by_role("button", name="Hand over to Strangers").click()
    expect(page.locator("#you")).to_have_text("You act as Vanguard, role: subcommander.")
    expect(page.locator("[data-team='Strangers']")).to_contain_text("commander")

    page.get_by_role("button", name="Leave alliance").click()
    page.get_by_role("button", name="Really leave").click()
    expect(page).to_have_url("/alliances/")
    expect(page.get_by_text("You have no alliances yet.")).to_be_visible()

    zed_page.goto(f"/alliance/{alliance['id']}")
    expect(zed_page.locator("[data-team='Vanguard']")).to_have_count(0)
    zed_page.get_by_role("button", name="Disband alliance").click()
    zed_page.get_by_role("button", name="Really disband").click()
    expect(zed_page).to_have_url("/alliances/")
    expect(zed_page.get_by_text("You have no alliances yet.")).to_be_visible()


def test_someone_outside_an_alliance_is_told_there_is_no_such_alliance(page, new_page, live_server, console):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    console.allow("status of 404")  # the browser logs the refusal itself
    outsider, _ = second_player(new_page, live_server, "Yan")
    outsider.goto(f"/alliance/{alliance['id']}")
    expect(outsider.get_by_role("heading", name="No such alliance")).to_be_visible()
    outsider.goto("/alliance/999999")
    expect(outsider.get_by_role("heading", name="No such alliance")).to_be_visible()


def test_a_commander_makes_the_alliance_page_and_writes_what_it_says(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    page.goto(f"/alliance/{alliance['id']}")
    card = page.locator("#alliance-page")
    expect(card).to_contain_text("This alliance has no page yet.")
    card.get_by_role("button", name="Make the page").click()
    expect(card.locator("#alliance-address")).to_contain_text("/p/alliance/")
    card.get_by_label("What the page says").fill("We hold the pass.")
    card.get_by_role("button", name="Save bio").click()
    expect(card.locator("#alliance-bio-note")).to_have_text("Bio saved.")
    address = card.locator("#alliance-address a").get_attribute("href")
    card.get_by_role("button", name="New address").click()
    expect(card.locator("#alliance-address a")).not_to_have_attribute("href", address)

    visitor = new_page()
    visitor.goto(card.locator("#alliance-address a").get_attribute("href"))
    expect(visitor.get_by_role("heading", name="Iron Pact")).to_be_visible()
    expect(visitor.get_by_text("We hold the pass.")).to_be_visible()


def test_a_member_without_the_role_does_not_get_the_commanders_controls(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    strangers = make_team(zed, "Strangers")
    mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": strangers["id"]})
    zed.post(f"/api/teams/{strangers['id']}/invitations/accept", {"alliance_id": alliance["id"]})
    zed_page.goto(f"/alliance/{alliance['id']}")
    expect(zed_page.locator("#you")).to_be_visible()
    for control in ("#invite-form", "#alliance-page", "#ballot-form", "#relation-form"):
        expect(zed_page.locator(control)).to_have_count(0)
    expect(zed_page.get_by_role("button", name="Disband alliance")).to_have_count(0)
    expect(zed_page.get_by_role("button", name="Remove Vanguard")).to_have_count(0)
    expect(zed_page.get_by_role("button", name="Leave alliance")).to_be_visible()


def test_the_alliance_votes_on_a_question_and_the_commander_closes_it(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    zed_page, zed = second_player(new_page, live_server, "Zed")
    strangers = make_team(zed, "Strangers")
    mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": strangers["id"]})
    zed.post(f"/api/teams/{strangers['id']}/invitations/accept", {"alliance_id": alliance["id"]})

    page.goto(f"/alliance/{alliance['id']}")
    form = page.locator("#ballot-form")
    form.get_by_label("Question").fill("Hold the pass?")
    form.get_by_label("Options, one a line").fill("Yes\nNo")
    form.get_by_role("button", name="Open ballot").click()
    ballot = page.locator("[data-ballot='Hold the pass?']")
    expect(ballot).to_be_visible()
    expect(ballot).to_contain_text("Open")

    zed_page.goto(f"/alliance/{alliance['id']}")
    other = zed_page.locator("[data-ballot='Hold the pass?']")
    other.get_by_role("button", name="Vote Yes").click()
    expect(other).to_contain_text("You voted Yes")
    expect(other).to_contain_text("Yes: 1")
    other.get_by_role("button", name="Vote No").click()  # a public ballot lets a team change its vote
    expect(other).to_contain_text("You voted No")
    expect(other.get_by_role("button", name="Close ballot")).to_have_count(0)  # a member's role may not close it

    ballot.get_by_role("button", name="Vote Yes").click()  # everyone who may vote has: it closes itself
    expect(ballot).to_contain_text("Closed")
    expect(ballot.get_by_role("button", name="Vote Yes")).to_have_count(0)


def test_a_commander_closes_a_ballot_early_and_a_secret_ballot_hides_who_voted(page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    rivals = make_team(mike, "Rivals")
    mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": rivals["id"]})
    mike.post(f"/api/teams/{rivals['id']}/invitations/accept", {"alliance_id": alliance["id"]})  # two voters: one vote does not close it
    page.goto(f"/alliance/{alliance['id']}")
    form = page.locator("#ballot-form")
    form.get_by_label("Question").fill("Who leads?")
    form.get_by_label("Options, one a line").fill("Vanguard\nRivals")
    form.get_by_label("Secret ballot").check()
    form.get_by_role("button", name="Open ballot").click()
    ballot = page.locator("[data-ballot='Who leads?']")
    expect(ballot).to_contain_text("Secret")
    ballot.get_by_role("button", name="Vote Vanguard").click()
    expect(ballot).to_contain_text("1 vote cast")
    expect(ballot).not_to_contain_text("Vanguard: 1")  # the totals wait for the close
    ballot.get_by_role("button", name="Close ballot").click()
    expect(ballot).to_contain_text("Closed")
    expect(ballot).to_contain_text("Vanguard: 1")


def test_a_ballot_with_too_few_options_says_why(page, live_server, console):
    console.allow("status of")  # the browser logs the refusal itself
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    page.goto(f"/alliance/{alliance['id']}")
    form = page.locator("#ballot-form")
    form.get_by_label("Question").fill("Only one?")
    form.get_by_label("Options, one a line").fill("Yes")
    form.get_by_role("button", name="Open ballot").click()
    expect(form.locator(".problem")).not_to_be_empty()
    expect(page.locator("[data-ballot]")).to_have_count(0)


def test_the_alliance_keeps_a_view_of_other_teams_and_forgets_it(page, new_page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    _, zed = second_player(new_page, live_server, "Zed")
    strangers = make_team(zed, "Strangers")

    page.goto(f"/alliance/{alliance['id']}")
    expect(page.get_by_text("The alliance has no views of anyone yet.")).to_be_visible()
    form = page.locator("#relation-form")
    form.get_by_label("Number").fill(str(strangers["id"]))
    form.get_by_label("Score").fill("40")
    form.get_by_label("Private note").fill("Held the east gate.")
    form.get_by_role("button", name="Save view").click()
    row = page.locator("[data-relationship='Strangers']")
    expect(row).to_contain_text("friendly")
    expect(row).to_contain_text("40")
    expect(row).to_contain_text("Held the east gate.")

    page.reload()
    expect(page.locator("[data-relationship='Strangers']")).to_contain_text("friendly")
    page.locator("[data-relationship='Strangers']").get_by_role("button", name="Forget Strangers").click()
    expect(page.locator("[data-relationship='Strangers']")).to_have_count(0)
    expect(page.get_by_text("The alliance has no views of anyone yet.")).to_be_visible()


def test_a_second_team_of_the_same_player_is_chosen_with_the_team_switch(page, live_server):
    live_server.make_account("Mike")
    mike = sign_in(page, "Mike")
    vanguard = make_team(mike, "Vanguard")
    rivals = make_team(mike, "Rivals")
    alliance = mike.post("/api/alliances", {"team_id": vanguard["id"], "name": "Iron Pact"})
    mike.post(f"/api/alliances/{alliance['id']}/invite", {"team_id": vanguard["id"], "target_team_id": rivals["id"]})
    mike.post(f"/api/teams/{rivals['id']}/invitations/accept", {"alliance_id": alliance["id"]})

    page.goto("/alliances/")
    expect(page.locator("[data-alliance='Iron Pact']")).to_have_count(2)  # once for each team in it
    page.locator("[data-alliance='Iron Pact'][data-team='Rivals']").get_by_role("link", name="Iron Pact").click()
    expect(page.locator("#you")).to_have_text("You act as Rivals, role: member.")
    page.get_by_label("Act as").select_option("Vanguard")
    expect(page.locator("#you")).to_have_text("You act as Vanguard, role: commander.")
