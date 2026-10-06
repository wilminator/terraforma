"""The game client's panels in a real browser: heroes, teams, the party and the nearby list."""

from playwright.sync_api import expect

from .test_smoke import log_in


def open_game(page, live_server):
    live_server.make_account()
    page.goto("/play/")
    log_in(page)
    expect(page.locator("#who")).to_have_text("Playing as Mike")


def tab(page, name):
    """Opens the panel (pressing the button of the one already open would close it)."""
    button = page.get_by_role("navigation", name="Game menu").get_by_role("button", name=name, exact=True)
    if button.get_attribute("aria-pressed") != "true":
        button.click()


def make_hero(page, name="Aria", job="fighter"):
    tab(page, "Heroes")
    page.locator("#hero-name").fill(name)
    page.locator("#hero-job").select_option(job)
    page.get_by_role("button", name="Make hero").click()
    expect(page.locator(f"[data-hero='{name}']")).to_be_visible()


def test_a_player_with_no_heroes_starts_on_the_heroes_panel_and_makes_one(page, live_server):
    open_game(page, live_server)
    expect(page.locator("#panel-heroes")).to_be_visible()
    expect(page.get_by_text("You have no heroes yet")).to_be_visible()

    page.locator("#hero-name").fill("Aria")
    page.locator("#hero-job").select_option("scout")
    page.get_by_role("button", name="Make hero").click()
    aria = page.locator("[data-hero='Aria']")
    expect(aria).to_contain_text("Scout, level 1")
    expect(aria).to_contain_text("on hub at (0, 0)")
    expect(aria).to_contain_text("Speed 6")

    page.reload()  # the server keeps them; the panel starts closed once there is a hero
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    expect(page.locator("#drawer")).to_be_hidden()
    tab(page, "Heroes")
    expect(page.locator("[data-hero='Aria']")).to_be_visible()


def test_a_refusal_is_said_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    open_game(page, live_server)
    make_hero(page)
    page.locator("#hero-name").fill("aria")
    page.get_by_role("button", name="Make hero").click()
    expect(page.locator("#panel-heroes .problem")).to_have_text("you already have a hero with that name")


def test_a_hero_is_renamed_and_deleted_after_a_second_press(page, live_server):
    open_game(page, live_server)
    make_hero(page)
    page.get_by_role("button", name="Rename Aria").click()
    page.get_by_label("New name for Aria").fill("Brin")
    page.get_by_role("button", name="Save").click()
    expect(page.locator("[data-hero='Brin']")).to_be_visible()
    expect(page.locator("[data-hero='Aria']")).to_have_count(0)

    page.get_by_role("button", name="Delete Brin").click()
    expect(page.locator("[data-hero='Brin']")).to_be_visible()  # one press only asks
    page.get_by_role("button", name="Delete Brin").click()
    expect(page.get_by_text("You have no heroes yet")).to_be_visible()


def test_heroes_are_put_on_a_team_and_taken_off_it(page, live_server):
    open_game(page, live_server)
    make_hero(page, "Aria")
    make_hero(page, "Brin", "scout")
    tab(page, "Teams")
    page.locator("#team-name").fill("Alpha")
    page.get_by_role("button", name="Make team").click()
    team = page.locator("[data-team='Alpha']")
    expect(team).to_be_visible()

    page.get_by_label("A hero to add to Alpha").select_option(label="Brin")
    team.get_by_role("button", name="Add", exact=True).click()
    page.get_by_label("A hero to add to Alpha").select_option(label="Aria")
    team.get_by_role("button", name="Add", exact=True).click()
    members = page.get_by_role("list", name="Heroes on Alpha").get_by_role("listitem")
    expect(members).to_have_text(["BrinRemove", "AriaRemove"])  # in the order they joined
    expect(team.get_by_text("Every hero is on a team.")).to_be_visible()

    page.get_by_role("button", name="Remove Brin from Alpha").click()
    expect(members).to_have_text(["AriaRemove"])

    page.get_by_role("button", name="Rename Alpha").click()
    page.get_by_label("New name for Alpha").fill("Omega")
    page.get_by_role("button", name="Save").click()
    expect(page.locator("[data-team='Omega']")).to_be_visible()


def test_a_team_plays_as_a_party_and_the_nearby_list_reads_the_heroes_place(page, live_server):
    open_game(page, live_server)
    make_hero(page)
    tab(page, "Teams")
    page.locator("#team-name").fill("Alpha")
    page.get_by_role("button", name="Make team").click()
    page.get_by_label("A hero to add to Alpha").select_option(label="Aria")
    page.locator("[data-team='Alpha']").get_by_role("button", name="Add", exact=True).click()

    tab(page, "Party")
    expect(page.get_by_text("Alpha: Aria")).to_be_visible()
    page.locator("#party-play").click()
    expect(page.locator("#panel-party .note")).to_contain_text("Alpha is in the game: party")
    expect(page.get_by_text("This team is not in a town.")).to_be_visible()

    tab(page, "Nearby")
    expect(page.locator("#nearby-hero")).to_contain_text("Aria")
    expect(page.get_by_text("Nothing in reach.")).to_be_visible()  # the example map has no one to talk to
    page.locator("#nearby-action").select_option("fight")
    expect(page.get_by_text("Nothing in reach.")).to_be_visible()


def test_a_team_cannot_play_with_nobody_on_it(page, live_server):
    open_game(page, live_server)
    make_hero(page)
    tab(page, "Teams")
    page.locator("#team-name").fill("Alpha")
    page.get_by_role("button", name="Make team").click()
    tab(page, "Party")
    expect(page.locator("#party-play")).to_be_disabled()


def test_the_menu_lists_the_engines_panels(page, live_server):
    open_game(page, live_server)
    names = page.get_by_role("navigation", name="Game menu").get_by_role("button")
    expect(names).to_have_text(["Heroes", "Teams", "Party", "Nearby"])
