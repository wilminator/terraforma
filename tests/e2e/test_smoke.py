"""The client in a real browser: the pages load clean, a game's module runs, and a player can log in, reload and log out."""

from playwright.sync_api import expect

from .conftest import PASSWORD


def log_in(page, username="Mike", password=PASSWORD):
    page.get_by_label("Username").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Log in").click()


def test_the_front_page_names_the_game_and_the_games_module_ran(page):
    page.goto("/")
    expect(page).to_have_title("TerraForma Example")
    expect(page.get_by_role("heading", name="TerraForma Example")).to_be_visible()
    # The example game's module changed the tagline through the shell's text hook, and listened for "ready".
    expect(page.get_by_text("The TerraForma example game: a tiny world to try the engine in.")).to_be_visible()
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    page.get_by_role("link", name="Play").click()
    expect(page).to_have_url("/play/")


def test_the_games_stylesheet_and_assets_are_served(page, live_server):
    page.goto("/")
    # The stylesheet is added by the shell once it has read the manifest, so wait for it to apply. (Not wait_for_function:
    # it evaluates a string in the page, which the Content-Security-Policy refuses, and it logs a console error.)
    expect(page.locator("html")).to_have_css("--accent", "#3b7f3a")
    answer = page.request.get("/assets/grass.svg")
    assert answer.ok and answer.headers["content-type"].startswith("image/svg")


def test_a_logged_out_player_is_sent_to_the_account_page_and_back_to_the_game_after_logging_in(page, live_server):
    live_server.make_account()
    page.goto("/play/")
    expect(page).to_have_url("/account/?next=play")
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    page.get_by_role("link", name="Create an account").click()  # the way to a new account is on the same page
    expect(page.get_by_role("heading", name="Create an account")).to_be_visible()
    page.get_by_role("link", name="Log in").click()

    log_in(page)
    expect(page).to_have_url("/play/")
    expect(page.locator("#who")).to_have_text("Playing as Mike")

    page.goto("/account/?next=play")  # already logged in: straight back to the game
    expect(page).to_have_url("/play/")


def test_the_account_page_goes_nowhere_but_the_game_after_logging_in(page, live_server):
    live_server.make_account()
    page.goto("/account/?next=https://example.com/")
    log_in(page)
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")


def test_a_player_logs_in_sees_the_stage_keeps_the_login_on_reload_and_logs_out(page, live_server):
    live_server.make_account()
    page.goto("/play/")
    log_in(page)
    expect(page.locator("#who")).to_have_text("Playing as Mike")
    expect(page.locator("#stage")).to_be_visible()
    assert int(page.locator("#stage").get_attribute("data-scale")) >= 1

    page.reload()
    expect(page.locator("#who")).to_have_text("Playing as Mike")

    # The CSRF token came back with the reload, so a call that changes something works: logging out needs it.
    page.get_by_role("button", name="Log out").click()
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()
    assert page.request.get("/api/session").json() == {"account": None, "csrf_token": None}


def test_a_wrong_password_is_said_plainly_and_nobody_is_logged_in(page, live_server, console):
    console.allow("status of 401")  # the browser logs the refusal itself
    live_server.make_account()
    page.goto("/play/")
    log_in(page, password="wrong horse battery")
    expect(page.get_by_role("alert").first).to_have_text("wrong username or password")
    expect(page.locator("#stage")).to_have_count(0)


def test_the_account_page_logs_in_and_out_too(page, live_server):
    live_server.make_account()
    page.goto("/account/")
    log_in(page)
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")
    page.get_by_role("button", name="Log out").click()
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()


def test_the_game_asks_for_landscape_and_the_pages_do_not(new_page, live_server):
    live_server.make_account()
    portrait = new_page({"width": 390, "height": 800})
    portrait.goto("/play/")
    expect(portrait.get_by_role("heading", name="Log in")).to_be_visible()  # the login page works upright
    log_in(portrait)
    expect(portrait.locator("#rotate")).to_be_visible()
    expect(portrait.locator("#rotate")).to_have_text("Turn your device sideways to play.")

    landscape = new_page({"width": 800, "height": 390})
    landscape.goto("/play/")
    log_in(landscape)
    expect(landscape.locator("#rotate")).to_be_hidden()
    expect(landscape.locator("#who")).to_have_text("Playing as Mike")

    # The marketing page is for any orientation.
    portrait.goto("/")
    expect(portrait.get_by_role("link", name="Play")).to_be_visible()


def test_the_stage_scales_in_whole_steps(new_page, live_server):
    live_server.make_account()
    page = new_page({"width": 1000, "height": 600})
    page.goto("/play/")
    log_in(page)
    expect(page.locator("#stage")).to_be_visible()
    # 480 by 270 fits twice in 1000 by about 560 (the status bar takes some of the height): scale 2, exactly 960 by 540.
    expect(page.locator("#stage")).to_have_attribute("data-scale", "2")
    box = page.locator("#stage").bounding_box()
    assert (box["width"], box["height"]) == (960, 540)

    page.set_viewport_size({"width": 500, "height": 400})
    expect(page.locator("#stage")).to_have_attribute("data-scale", "1")
