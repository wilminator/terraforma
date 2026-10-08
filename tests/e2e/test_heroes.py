"""Teams in a real browser: a team is made in the game client's Teams panel, then filled in on its own page; plus the party and nearby panels."""

import re

from playwright.sync_api import expect

from .test_smoke import log_in


def open_game(page, live_server, username="Mike"):
    live_server.make_account(username)
    page.goto("/play/")
    log_in(page, username)
    expect(page.locator("#who")).to_have_text(f"Playing as {username}")


def tab(page, name):
    """Opens the panel (pressing the button of the one already open would close it)."""
    button = page.get_by_role("navigation", name="Game menu").get_by_role("button", name=name, exact=True)
    if button.get_attribute("aria-pressed") != "true":
        button.click()


def add_hero(page, name, job="fighter"):
    """Fills the add-a-hero form on a team's page."""
    page.locator("#new-hero").fill(name)
    page.locator("#new-hero-job").select_option(job)
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.locator(f"[data-hero='{name}']")).to_be_visible()


def new_team(page, name="Alpha"):
    """Makes a team in the Teams panel; its page opens."""
    tab(page, "Teams")
    page.locator("#team-name").fill(name)
    page.locator("#team-save").click()
    expect(page).to_have_url(re.compile(r"/team/\d+$"))
    expect(page.locator("#team-title")).to_have_text(name)


def make_team(page, name="Alpha", heroes=(("Aria", "fighter"),)):
    """A team with its heroes, then back in the game."""
    new_team(page, name)
    for hero, job in heroes:
        add_hero(page, hero, job)
    back_to_game(page)


def back_to_game(page):
    page.get_by_role("link", name="Back to the game").click()
    expect(page).to_have_url("/play/")
    expect(page.locator("#who")).to_be_visible()


def test_a_player_with_no_teams_starts_on_the_teams_panel_makes_one_and_fills_it_on_its_page(page, live_server):
    open_game(page, live_server)
    expect(page.locator("#panel-teams")).to_be_visible()
    expect(page.get_by_text("You have no teams yet")).to_be_visible()
    expect(page.locator("#team-count")).to_have_text("Teams: 0 of 3")

    new_team(page, "Alpha")
    expect(page.locator("#team-count")).to_have_text("0 of 4 heroes")
    expect(page.locator("#team-incomplete")).to_contain_text("needs at least 1 heroes before it can play")
    page.locator("#new-hero").fill("Aria")
    page.locator("#new-hero-job").select_option("scout")
    page.get_by_role("button", name="Add", exact=True).click()
    aria = page.locator("[data-hero='Aria']")
    expect(aria).to_contain_text("Scout, level 1")
    expect(aria).to_contain_text("on hub at (0, 0)")
    expect(aria).to_contain_text("Speed 6")
    expect(page.locator("#team-count")).to_have_text("1 of 4 heroes")
    expect(page.locator("#team-incomplete")).to_have_count(0)

    back_to_game(page)
    tab(page, "Teams")
    expect(page.locator("[data-team='Alpha']")).to_contain_text("1 of 4 heroes")
    expect(page.locator("[data-team='Alpha']")).to_contain_text("Aria")
    expect(page.locator("#team-count")).to_have_text("Teams: 1 of 3")
    page.get_by_role("link", name="Open Alpha").click()
    expect(page.locator("[data-hero='Aria']")).to_be_visible()


def test_a_team_page_takes_heroes_up_to_the_games_maximum(page, live_server):
    open_game(page, live_server)
    new_team(page, "Alpha")
    for number in range(1, 5):
        add_hero(page, f"Hero{number}")
    expect(page.locator("#add-hero-form")).to_have_count(0)  # four is the example's maximum
    expect(page.get_by_text("This team is full.")).to_be_visible()
    expect(page.locator("#team-count")).to_have_text("4 of 4 heroes")


def test_a_refusal_is_said_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"),))
    new_team(page, "Beta")
    page.locator("#new-hero").fill("aria")
    page.get_by_role("button", name="Add", exact=True).click()
    expect(page.locator("#team-editor .problem")).to_have_text("you already have a hero with that name")
    expect(page.locator("[data-hero]")).to_have_count(0)


def test_a_team_name_is_checked_by_the_server(page, live_server, console):
    console.allow("status of 422")
    open_game(page, live_server)
    page.locator("#team-name").fill("-")
    page.locator("#team-save").click()
    expect(page.locator("#panel-teams .problem")).to_contain_text("a name is 2-24 letters")
    expect(page).to_have_url("/play/")


def test_a_team_and_a_hero_are_renamed_on_the_teams_page(page, live_server):
    open_game(page, live_server)
    new_team(page, "Alpha")
    add_hero(page, "Aria")
    add_hero(page, "Bob", "scout")
    page.get_by_role("button", name="Rename Aria").click()
    page.get_by_label("New name for Aria").fill("Brin")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("[data-hero='Brin']")).to_be_visible()
    page.get_by_role("button", name="Rename Alpha").click()
    page.get_by_label("New name for Alpha").fill("Omega")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("#team-title")).to_have_text("Omega")


def test_a_hero_is_removed_and_the_empty_place_filled_in_a_game_that_allows_it(page, live_server):
    open_game(page, live_server)
    new_team(page, "Alpha")
    add_hero(page, "Aria")
    add_hero(page, "Bob", "scout")
    page.get_by_role("button", name="Remove Aria from Alpha").click()
    expect(page.locator("[data-hero='Aria']")).to_have_count(0)
    expect(page.locator("#team-count")).to_have_text("1 of 4 heroes")
    add_hero(page, "Cato", "scout")
    expect(page.locator("[data-hero='Cato']")).to_contain_text("Scout")
    expect(page.locator("#team-count")).to_have_text("2 of 4 heroes")


def test_a_hero_is_replaced_in_the_same_place(page, live_server):
    open_game(page, live_server)
    new_team(page, "Alpha")
    add_hero(page, "Aria")
    add_hero(page, "Bob", "scout")
    page.get_by_role("button", name="Replace Aria").click()
    page.get_by_label("Name of the hero replacing Aria").fill("Dax")
    page.get_by_label("Job of the hero replacing Aria").select_option("scout")
    page.get_by_role("button", name="Replace", exact=True).click()
    heroes = page.locator("[data-hero]")
    expect(heroes).to_have_count(2)
    expect(heroes.first).to_have_attribute("data-hero", "Dax")  # in Aria's place
    expect(page.locator("[data-hero='Aria']")).to_have_count(0)


def test_a_team_below_the_minimum_says_so_and_cannot_play(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha")
    tab(page, "Teams")
    page.get_by_role("link", name="Open Alpha").click()
    page.get_by_role("button", name="Remove Aria from Alpha").click()
    expect(page.locator("#team-incomplete")).to_be_visible()
    back_to_game(page)
    tab(page, "Party")
    expect(page.locator("#party-play")).to_be_disabled()


def test_moving_a_hero_to_another_team_is_refused_in_a_game_that_does_not_allow_it(page, live_server, console):
    console.allow("status of 422")
    open_game(page, live_server)
    make_team(page, "Beta", (("Cato", "fighter"),))
    new_team(page, "Alpha")
    add_hero(page, "Aria")
    add_hero(page, "Bob", "scout")
    change = page.get_by_label("Move or exchange Aria", exact=True)
    change.select_option(label="Move to Beta")
    page.get_by_role("button", name="Move or exchange Aria as chosen").click()
    expect(page.locator("#team-editor .problem")).to_have_text("heroes cannot be moved between teams in this game")
    expect(page.locator("[data-hero='Aria']")).to_be_visible()
    expect(change.locator("option")).to_have_text(["Move to Beta", "Exchange with Cato of Beta"])


def test_deleting_a_team_takes_its_heroes_after_a_second_press_and_returns_to_the_game(page, live_server):
    open_game(page, live_server)
    new_team(page, "Alpha")
    add_hero(page, "Aria")
    delete = page.get_by_role("button", name="Delete Alpha and its heroes")
    delete.click()
    expect(delete).to_have_text("Really delete?")
    expect(page.locator("#team-title")).to_be_visible()  # one press only asks
    delete.click()
    expect(page).to_have_url("/play/")
    expect(page.get_by_text("You have no teams yet")).to_be_visible()


def test_the_form_goes_when_the_player_has_all_the_teams_the_game_allows(page, live_server):
    open_game(page, live_server)
    for name in ("Alpha", "Beta", "Gamma"):
        make_team(page, name, ((f"Hero{name}", "fighter"),))
    tab(page, "Teams")
    expect(page.locator("#team-count")).to_have_text("Teams: 3 of 3")
    expect(page.locator("#team-form")).to_have_count(0)


def test_a_team_page_is_private_and_needs_a_login(new_page, live_server):
    mike = new_page()
    open_game(mike, live_server)
    new_team(mike, "Alpha")
    address = mike.url

    ann = new_page()
    open_game(ann, live_server, "Ann")
    ann.goto(address)
    expect(ann.get_by_text("You have no team with that address.")).to_be_visible()
    expect(ann.get_by_role("link", name="Back to the game")).to_be_visible()

    stranger = new_page()
    stranger.goto(address)
    expect(stranger).to_have_url("/account/?next=play")


def test_a_team_plays_as_a_party_and_the_nearby_list_reads_the_heroes_place(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha")
    tab(page, "Party")
    expect(page.get_by_text("Alpha: Aria")).to_be_visible()
    page.locator("#party-play").click()
    expect(page.locator("#panel-party .note")).to_contain_text("Alpha is in the game: party")
    expect(page.get_by_text("This team is not in a town.")).to_be_visible()

    tab(page, "Nearby")
    expect(page.locator("#nearby-hero")).to_contain_text("Aria")
    expect(page.locator("#panel-nearby [data-kind='npc']")).to_have_count(3)  # the example's three people next to where heroes start


def test_the_menu_lists_the_engines_panels(page, live_server):
    open_game(page, live_server)
    names = page.get_by_role("navigation", name="Game menu").get_by_role("button")
    expect(names).to_have_text(["Teams", "Party", "Nearby", "Talk"])
