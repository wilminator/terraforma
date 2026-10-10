"""The map on the stage, in a real browser: the party shows up on its map, walks where it is told (the server decides each tile), and uses
the sign and the stairs, all on the one page. The example's hub is 16 by 10 tiles of 32 pixels, a little larger than the stage's 480 by
270, and its town is the start tile only, so the party walks as soon as it steps off it."""

from playwright.sync_api import expect

from .test_heroes import make_team, open_game, tab

SLOW = 20_000  # a walk of a dozen tiles takes a couple of seconds
SIZE = {"hub": (16, 10), "cellar": (8, 5)}  # the example's maps, in tiles


def play(page, live_server):
    """A logged-in player whose team has entered the game."""
    open_game(page, live_server)
    make_team(page, "Alpha")
    tab(page, "Party")
    page.locator("#party-play").click()
    expect(page.locator("#stage")).to_have_attribute("data-map", "hub")


def tap(page, x, y):
    """Taps tile (x, y) of the hub: the view is centered on the party but held inside the 512 by 320 map, so it scrolls a little."""
    stage = page.locator("#stage")
    scale = int(stage.get_attribute("data-scale"))
    px, py = int(stage.get_attribute("data-party-x")), int(stage.get_attribute("data-party-y"))
    width, height = SIZE[stage.get_attribute("data-map")]

    def corner(center, size, view):  # (a map smaller than the view sits in the middle of it)
        return (size - view) / 2 if size <= view else min(max(center - view / 2, 0), size - view)

    left, top = corner(32 * px + 16, 32 * width, 480), corner(32 * py + 16, 32 * height, 270)
    page.locator("#stage-view").click(position={"x": (32 * x + 16 - left) * scale, "y": (32 * y + 16 - top) * scale})


def at(page, x, y):
    expect(page.locator("#stage")).to_have_attribute("data-party-x", str(x), timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-party-y", str(y), timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-walking", "false", timeout=SLOW)


def test_before_a_team_plays_the_stage_has_no_map(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha")
    expect(page.locator("#stage")).not_to_have_attribute("data-map", "hub")
    expect(page.locator("#stage")).to_have_attribute("aria-label", "The game")


def test_the_stage_draws_the_tiles_of_the_games_tileset(page, live_server):
    play(page, live_server)
    at(page, 0, 0)
    # (the hub names 13 kinds of tile, each a frame of the sheet tiles.png; the stage says how many pictures it loaded)
    expect(page.locator("#stage")).to_have_attribute("data-art", "13")
    assert page.request.get("/assets/tiles.png").ok and page.request.get("/assets/tiles.sheet.json").json()["frames"] == 13


def test_a_team_that_plays_appears_on_the_hubs_map_where_heroes_start(page, live_server):
    play(page, live_server)
    at(page, 0, 0)
    expect(page.locator("#stage")).to_have_attribute("aria-label", "The Meadow: the party is at (0, 0)")


def test_a_tap_walks_the_party_there_and_the_server_says_no_to_a_wall(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    play(page, live_server)
    tap(page, 3, 1)
    at(page, 3, 1)
    tap(page, 0, 3)  # forest
    expect(page.locator("#stage-note")).to_have_text("(0, 3) can't be walked on")
    at(page, 3, 1)


def test_the_arrow_keys_walk_the_party_a_tile_at_a_time(page, live_server):
    play(page, live_server)
    page.locator("#stage").focus()
    page.keyboard.press("ArrowRight")
    at(page, 1, 0)
    page.keyboard.press("s")
    at(page, 1, 1)


def test_the_party_reads_a_sign_next_to_it_and_the_server_refuses_one_that_is_far(page, live_server, console):
    console.allow("status of 409")
    play(page, live_server)
    tap(page, 2, 5)  # the signpost, far away
    expect(page.locator("#stage-note")).not_to_be_empty()
    expect(page.locator("#stage-note")).not_to_contain_text("Welcome to the Meadow")
    tap(page, 2, 4)
    at(page, 2, 4)
    tap(page, 2, 5)
    expect(page.locator("#stage-note")).to_contain_text("Welcome to the Meadow.")


def test_the_stairs_take_the_party_down_to_the_cellar_and_back_without_leaving_the_page(page, live_server):
    play(page, live_server)
    page.evaluate("window.__sameDocument = true")
    tap(page, 8, 6)
    at(page, 8, 6)
    tap(page, 8, 7)  # the cellar stairs
    expect(page.locator("#panel-dialog")).to_be_visible()
    expect(page.locator(".transcript")).to_contain_text("The stairs lead down.")
    page.locator("[data-choice='0']").click()  # Go down
    expect(page.locator("#stage")).to_have_attribute("data-map", "cellar", timeout=SLOW)
    at(page, 1, 1)

    tap(page, 1, 1)  # the stairs up, under the party
    expect(page.locator(".transcript")).to_contain_text("The stairs lead up.")
    page.locator("[data-choice='0']").click()  # Go up
    expect(page.locator("#stage")).to_have_attribute("data-map", "hub", timeout=SLOW)
    at(page, 8, 7)
    expect(page).to_have_url("/play/")
    assert page.evaluate("window.__sameDocument") is True, "the page was never reloaded or left"


def test_a_stage_that_is_reloaded_picks_the_party_up_where_it_stands(page, live_server):
    play(page, live_server)
    tap(page, 3, 1)
    at(page, 3, 1)
    page.reload()
    expect(page.locator("#who")).to_be_visible()
    tab(page, "Party")  # (the team that is playing is chosen again)
    expect(page.locator("#stage")).to_have_attribute("data-map", "hub")
    at(page, 3, 1)
