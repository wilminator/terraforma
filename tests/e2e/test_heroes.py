"""The game client's panels in a real browser: teams made with their heroes, the party and the nearby list."""

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


def make_team(page, name="Alpha", heroes=(("Aria", "fighter"),)):
    """Fills the new-team form (a row per hero) and saves it."""
    tab(page, "Teams")
    page.locator("#team-name").fill(name)
    for number, (hero, job) in enumerate(heroes, start=1):
        if number > 1:
            page.locator("#add-draft-hero").click()
        page.locator(f"#new-hero-{number}").fill(hero)
        page.locator(f"#new-job-{number}").select_option(job)
    page.locator("#team-save").click()
    expect(page.locator(f"[data-team='{name}']")).to_be_visible()


def test_a_player_with_no_teams_starts_on_the_teams_panel_and_makes_one_with_its_heroes(page, live_server):
    open_game(page, live_server)
    expect(page.locator("#panel-teams")).to_be_visible()
    expect(page.get_by_text("You have no teams yet")).to_be_visible()
    expect(page.locator("#team-count")).to_have_text("Teams: 0 of 3")
    save = page.locator("#team-save")
    expect(save).to_be_disabled()
    expect(page.get_by_text("A team needs 1 to 4 heroes")).to_be_visible()

    page.locator("#team-name").fill("Alpha")
    expect(save).to_be_disabled()  # the hero has no name yet
    page.locator("#new-hero-1").fill("Aria")
    page.locator("#new-job-1").select_option("scout")
    expect(save).to_be_enabled()
    save.click()

    aria = page.locator("[data-team='Alpha'] [data-hero='Aria']")
    expect(aria).to_contain_text("Scout, level 1")
    expect(aria).to_contain_text("on hub at (0, 0)")
    expect(aria).to_contain_text("Speed 6")
    expect(page.locator("#team-count")).to_have_text("Teams: 1 of 3")

    page.reload()  # the server keeps them; the panel starts closed once there is a team
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    expect(page.locator("#drawer")).to_be_hidden()
    tab(page, "Teams")
    expect(page.locator("[data-team='Alpha'] [data-hero='Aria']")).to_be_visible()


def test_the_form_takes_up_to_the_games_maximum_of_heroes(page, live_server):
    open_game(page, live_server)
    page.locator("#team-name").fill("Alpha")
    for number in range(1, 5):
        if number > 1:
            page.locator("#add-draft-hero").click()
        page.locator(f"#new-hero-{number}").fill(f"Hero{number}")
    expect(page.locator("#add-draft-hero")).to_have_count(0)  # four is the example's maximum
    expect(page.locator("#team-save")).to_be_enabled()
    page.get_by_role("button", name="Drop hero 4").click()
    expect(page.locator("#new-hero-4")).to_have_count(0)
    expect(page.locator("#add-draft-hero")).to_be_visible()
    expect(page.get_by_role("button", name="Drop hero 1")).to_have_count(1)  # (a row can go while there are more than the minimum)
    page.locator("#team-save").click()
    expect(page.locator("[data-team='Alpha'] [data-hero]")).to_have_count(3)


def test_a_refusal_is_said_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"),))
    page.locator("#team-name").fill("Beta")
    page.locator("#new-hero-1").fill("aria")
    page.locator("#team-save").click()
    expect(page.locator("#panel-teams .problem")).to_have_text("you already have a hero with that name")
    expect(page.locator("[data-team='Beta']")).to_have_count(0)


def test_a_team_and_a_hero_are_renamed(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"), ("Bob", "scout")))
    page.get_by_role("button", name="Rename Aria").click()
    page.get_by_label("New name for Aria").fill("Brin")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("[data-hero='Brin']")).to_be_visible()
    page.get_by_role("button", name="Rename Alpha").click()
    page.get_by_label("New name for Alpha").fill("Omega")
    page.get_by_role("button", name="Save", exact=True).click()
    expect(page.locator("[data-team='Omega'] [data-hero='Brin']")).to_be_visible()


def test_a_hero_is_removed_and_the_empty_place_filled_in_a_game_that_allows_it(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"), ("Bob", "scout")))
    page.get_by_role("button", name="Remove Aria from Alpha").click()
    expect(page.locator("[data-hero='Aria']")).to_have_count(0)
    expect(page.locator("[data-team='Alpha']")).to_contain_text("1 of 4 heroes")

    page.get_by_label("Name of a new hero for Alpha").fill("Cato")
    page.get_by_label("Job of a new hero for Alpha").select_option("scout")
    page.locator("[data-team='Alpha']").get_by_role("button", name="Add", exact=True).click()
    expect(page.locator("[data-hero='Cato']")).to_contain_text("Scout")
    expect(page.locator("[data-team='Alpha']")).to_contain_text("2 of 4 heroes")


def test_a_hero_is_replaced_in_the_same_place(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"), ("Bob", "scout")))
    page.get_by_role("button", name="Replace Aria").click()
    page.get_by_label("Name of the hero replacing Aria").fill("Dax")
    page.get_by_label("Job of the hero replacing Aria").select_option("scout")
    page.get_by_role("button", name="Replace", exact=True).click()
    heroes = page.locator("[data-team='Alpha'] [data-hero]")
    expect(heroes).to_have_count(2)
    expect(heroes.first).to_have_attribute("data-hero", "Dax")  # in Aria's place
    expect(page.locator("[data-hero='Aria']")).to_have_count(0)


def test_a_team_below_the_minimum_says_so_and_cannot_play(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha")
    page.get_by_role("button", name="Remove Aria from Alpha").click()
    expect(page.get_by_text("needs at least 1 heroes before it can play")).to_be_visible()
    tab(page, "Party")
    expect(page.locator("#party-play")).to_be_disabled()


def test_moving_a_hero_to_another_team_is_refused_in_a_game_that_does_not_allow_it(page, live_server, console):
    console.allow("status of 422")
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"), ("Bob", "scout")))
    make_team(page, "Beta", (("Cato", "fighter"),))
    change = page.get_by_label("Move or exchange Aria", exact=True)
    change.select_option(label="Move to Beta")
    page.get_by_role("button", name="Move or exchange Aria as chosen").click()
    expect(page.locator("#panel-teams .problem")).to_have_text("heroes cannot be moved between teams in this game")
    expect(page.locator("[data-team='Alpha'] [data-hero='Aria']")).to_be_visible()
    expect(change.locator("option")).to_have_text(["Move to Beta", "Exchange with Cato of Beta"])


def test_deleting_a_team_takes_its_heroes_after_a_second_press(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha", (("Aria", "fighter"), ("Bob", "scout")))
    delete = page.get_by_role("button", name="Delete Alpha and its heroes")
    delete.click()
    expect(delete).to_have_text("Really delete?")
    expect(page.locator("[data-team='Alpha']")).to_be_visible()  # one press only asks
    delete.click()
    expect(page.get_by_text("You have no teams yet")).to_be_visible()
    expect(page.locator("[data-hero]")).to_have_count(0)


def test_the_form_goes_when_the_player_has_all_the_teams_the_game_allows(page, live_server):
    open_game(page, live_server)
    for number, name in enumerate(("Alpha", "Beta", "Gamma"), start=1):
        make_team(page, name, ((f"Hero{number}", "fighter"),))
    expect(page.locator("#team-count")).to_have_text("Teams: 3 of 3")
    expect(page.locator("#team-form")).to_have_count(0)
    delete = page.get_by_role("button", name="Delete Gamma and its heroes")
    delete.click()
    delete.click()
    expect(page.locator("#team-form")).to_be_visible()


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
    expect(page.get_by_text("Nothing in reach.")).to_be_visible()  # the example map has no one to talk to
    page.locator("#nearby-action").select_option("fight")
    expect(page.get_by_text("Nothing in reach.")).to_be_visible()


def test_the_menu_lists_the_engines_panels(page, live_server):
    open_game(page, live_server)
    names = page.get_by_role("navigation", name="Game menu").get_by_role("button")
    expect(names).to_have_text(["Teams", "Party", "Nearby"])
