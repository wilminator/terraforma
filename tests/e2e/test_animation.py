"""The animation player in a real browser: which animation plays for a name, the transforms (aliases and frames), the shared timer, reduced
motion, and the example game's animated tile on the map. Most tests hand the module a clock they move by hand, so no test waits for time."""

from playwright.sync_api import expect

from .test_heroes import make_team, open_game, tab
from .test_map import SLOW, at, tap

LOAD = "const A = await import('/client/js/animation.js'); const Art = await import('/client/js/art.js');"
# A clock that only moves when the test says so, and a "random" that is a number the test chooses.
CLOCK = """
    let time = 0, chance = 0.5, still = false;
    const clock = new A.Clock({ now: () => time, request: () => 1, cancel: () => {}, still: () => still, random: () => chance });
"""


def run(page, script):
    page.goto("/")
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    return page.evaluate("async () => {" + LOAD + CLOCK + script + "}")


def test_the_names_tried_for_an_action_end_at_stand_and_then_base(page):
    got = run(
        page,
        """return {
            fight: A.candidates("hit"),
            map: A.candidates("move", "left"),
            stand: A.candidates("stand", "up"),
        };""",
    )
    assert got["fight"] == ["hit", "hit_down", "stand", "stand_down", "base"]
    assert got["map"] == ["move_left", "move", "move_down", "stand_left", "stand", "stand_down", "base"]
    assert got["stand"] == ["stand_up", "stand", "stand_down", "base"]


def test_a_missing_name_falls_back_to_a_standing_picture_then_the_first_animation_called_base(page):
    got = run(
        page,
        """const map = A.parseAnimations({ animations: { stand_down: { frames: [0] }, stand_left: { frames: [1] }, move_down: { frames: [2] } } }, 3, clock);
        const fight = A.parseAnimations({ animations: { base: { frames: [0] }, hit: { frames: [1] } } }, 2, clock);
        return {
            exact: map.find("move", "down"), noDirection: map.find("move", "right"),
            moveToStand: A.parseAnimations({ animations: { stand_down: { frames: [0] }, stand_left: { frames: [1] } } }, 2, clock).find("move", "left"),
            cast: map.find("cast", "left"), castDown: map.find("cast"), nothing: A.parseAnimations({ animations: { jump: { frames: [0] } } }, 1, clock).find("hit"),
            hit: fight.find("hit"), flee: fight.find("flee"), playNothing: A.parseAnimations({ animations: { jump: { frames: [0] } } }, 1, clock).play("hit"),
        };""",
    )
    assert got == {
        "exact": "move_down", "noDirection": "move_down", "moveToStand": "stand_left",
        "cast": "stand_left", "castDown": "stand_down", "nothing": None, "hit": "hit", "flee": "base", "playNothing": None,
    }  # (a missing direction is down, a missing move or cast is standing, and a sheet with no standing picture has nothing to play)


def test_an_alias_is_its_animation_with_the_aliases_transform_after_each_frames_own(page):
    got = run(
        page,
        """const set = A.parseAnimations({
            animations: { stand_right: { frames: [{ frame: 0, rotate: 90 }, 1], ms: 50 } },
            aliases: { stand_left: { of: "stand_right", flip_x: true }, stand_up: { of: "stand_left", scale: 2 } },
        }, 2, clock);
        const ops = (name) => set.clip(name).frames.map((each) => each.ops.map((op) => [op.flip_x, op.scale, op.rotate]));
        return { names: set.names(), right: ops("stand_right"), left: ops("stand_left"), up: ops("stand_up"), ms: set.clip("stand_up").frames.map((each) => each.ms) };""",
    )
    assert got["names"] == ["stand_right", "stand_left", "stand_up"]
    assert got["right"] == [[[False, 1, 90]], []], "a frame with no transform has none to apply"
    assert got["left"] == [[[False, 1, 90], [True, 1, 0]], [[True, 1, 0]]]
    assert got["up"] == [[[False, 1, 90], [True, 1, 0], [False, 2, 0]], [[True, 1, 0], [False, 2, 0]]], "the frame's, then the alias nearest the animation, then the next"
    assert got["ms"] == [50, 50]


def test_the_transform_matrix_flips_scales_and_rotates_about_the_center_or_a_pivot(page):
    got = run(
        page,
        """const near = (m) => m.map((n) => Math.round(n * 1e6) / 1e6 + 0);
        return {
            none: near(A.matrixOf([], 16, 16)),
            flipX: near(A.matrixOf([{ flip_x: true, flip_y: false, scale: 1, rotate: 0, pivot: null }], 16, 16)),
            flipY: near(A.matrixOf([{ flip_x: false, flip_y: true, scale: 1, rotate: 0, pivot: null }], 16, 8)),
            turn: near(A.matrixOf([{ flip_x: false, flip_y: false, scale: 1, rotate: 90, pivot: null }], 16, 16)),
            grow: near(A.matrixOf([{ flip_x: false, flip_y: false, scale: 2, rotate: 0, pivot: [0, 0] }], 16, 16)),
            both: near(A.matrixOf([
                { flip_x: true, flip_y: false, scale: 1, rotate: 0, pivot: null },
                { flip_x: false, flip_y: false, scale: 1, rotate: 180, pivot: null },
            ], 16, 16)),
        };""",
    )
    assert got["none"] == [1, 0, 0, 1, 0, 0]
    assert got["flipX"] == [-1, 0, 0, 1, 16, 0], "x becomes 16 - x"
    assert got["flipY"] == [1, 0, 0, -1, 0, 8], "y becomes 8 - y"
    assert got["turn"] == [0, 1, -1, 0, 16, 0], "a quarter turn clockwise about (8, 8): the point (0, 0) goes to (16, 0)"
    assert got["grow"] == [2, 0, 0, 2, 0, 0]
    assert got["both"] == [1, 0, 0, -1, 0, 16], "flip x, then turn half way about the center: a flip of y"


def test_a_looping_animation_goes_round_and_a_one_shot_holds_its_last_frame(page):
    got = run(
        page,
        """const set = A.parseAnimations({ animations: {
            walk: { frames: [0, { frame: 1, ms: 300 }, 2], ms: 100 },
            hit: { frames: [3, 4], ms: 100, loop: false },
        } }, 5, clock);
        const walk = set.play("walk");
        const hit = set.play("hit");
        const seen = [];
        for (const at of [0, 99, 100, 399, 400, 499, 500, 1000, 1100]) { time = at; seen.push([walk.frameAt().frame, hit.frameAt().frame, hit.done]); }
        return seen;""",
    )
    assert [each[0] for each in got] == [0, 0, 1, 1, 2, 2, 0, 0, 1], "frames of 100, 300 and 100 ms, over and over (shared sync counts from the clock's start)"
    assert [each[1] for each in got] == [3, 3, 4, 4, 4, 4, 4, 4, 4], "a one-shot stays on its last frame"
    assert [each[2] for each in got] == [False, False, False, True, True, True, True, True, True]


def test_a_looping_animation_asked_to_play_once_stops_on_its_last_frame(page):
    got = run(
        page,
        """const set = A.parseAnimations({ animations: { walk: { frames: [0, 1, 2], ms: 100 } } }, 3, clock);
        const once = set.play("walk", { once: true });
        const seen = [];
        for (const at of [0, 150, 299, 300, 900]) { time = at; seen.push([once.frameAt().frame, once.done]); }
        return seen;""",
    )
    assert got == [[0, False], [1, False], [2, False], [2, True], [2, True]]


def test_one_shot_and_random_players_count_from_when_they_started_and_shared_ones_all_agree(page):
    got = run(
        page,
        """const set = A.parseAnimations({ animations: {
            shared: { frames: [0, 1], ms: 100 }, drift: { frames: [0, 1], ms: 100, sync: "random" }, once: { frames: [0, 1], ms: 100, loop: false },
        } }, 2, clock);
        time = 1000;
        const early = set.play("shared");
        time = 1130;
        const late = set.play("shared");
        chance = 0.6; // (60% of the way round 200 ms: 120 ms in, so frame 1)
        const drifting = set.play("drift");
        const once = set.play("once");
        const at = (time_) => { time = time_; return [early.frameAt().frame, late.frameAt().frame, drifting.frameAt().frame, once.frameAt().frame]; };
        return [at(1130), at(1180), at(1290), at(1330)];""",
    )
    # (a shared player is where the clock is, whenever it began: 1130 ms is 130 into a 200 ms round, so frame 1; the drifting one began 120 ms
    # into its round; the one-shot counts from its own start at 1130 and holds its last frame)
    assert got == [[1, 1, 1, 0], [1, 1, 1, 0], [0, 0, 0, 1], [1, 1, 1, 1]]


def test_with_reduced_motion_everything_shows_its_first_frame_and_no_timer_runs(page):
    got = run(
        page,
        """const set = A.parseAnimations({ animations: { walk: { frames: [3, 4, 5] } } }, 6, clock);
        const player = set.play("walk");
        still = true; time = 250;
        let ticks = 0;
        let requested = 0;
        const quiet = new A.Clock({ now: () => time, request: () => { requested++; return 1; }, cancel: () => {}, still: () => true });
        quiet.subscribe(() => ticks++);
        return { frame: player.frameAt().frame, requested, ticks };""",
    )
    assert got == {"frame": 3, "requested": 0, "ticks": 0}


def test_the_clock_runs_one_loop_for_all_listeners_and_stops_when_none_are_left(page):
    got = run(
        page,
        """const waiting = [];
        let cancelled = 0;
        const loop = new A.Clock({ now: () => time, request: (callback) => waiting.push(callback), cancel: () => { cancelled++; waiting.length = 0; }, still: () => false });
        const heard = [];
        const stopA = loop.subscribe((now) => heard.push(["a", now]));
        const stopB = loop.subscribe((now) => heard.push(["b", now]));
        const requestedForTwo = waiting.length;
        time = 16; waiting.shift()();
        time = 32; waiting.shift()();
        stopA();
        time = 48; waiting.shift()();
        stopB();
        return { requestedForTwo, heard, left: waiting.length, cancelled };""",
    )
    assert got["requestedForTwo"] == 1, "two listeners, one loop"
    assert got["heard"] == [["a", 16], ["b", 16], ["a", 32], ["b", 32], ["b", 48]]
    assert got["left"] == 0 and got["cancelled"] == 1


def test_a_bad_animation_file_is_refused_with_the_reason(page):
    got = run(
        page,
        """const refused = (data, frames = 2) => { try { A.parseAnimations(data, frames, clock); return null; } catch (error) { return error.message; } };
        return [
            refused({ animations: { a: { frames: [0] } }, aliases: { b: { of: "c" }, c: { of: "b" } } }),
            refused({ animations: { a: { frames: [0] } }, aliases: { b: { of: "nothing" } } }),
            refused({ animations: { a: { frames: [0, 2] } } }),
            refused({ animations: { a: { frames: [] } } }),
            refused({ animations: { Bad: { frames: [0] } } }),
            refused({ animations: { a: { frames: [0] } }, aliases: { a: { of: "a" } } }),
            refused({ animations: { a: { frames: [{ frame: 0, scale: 0 }] } } }),
            refused({ animations: { a: { frames: [0, 1] } } }),
        ];""",
    )
    assert "loop" in got[0] and "nothing" in got[1] and "frame 2" in got[2] and "needs frames" in got[3]
    assert "lowercase" in got[4] and "both" in got[5] and "scale" in got[6] and got[7] is None


def test_the_example_crystals_animations_are_loaded_with_its_sheet_and_drawn_flipped_by_an_alias(page):
    got = run(
        page,
        """const art = new Art.Art((name) => `/assets/${name}`, { clock });
        const loaded = await art.load({ sheet: "crystal", animations: "crystal" });
        const again = await art.load({ sheet: "crystal", animations: "crystal" });
        const plain = await art.load({ sheet: "crystal" });
        const base = loaded.animations.play("base");
        const mirror = loaded.animations.play("mirror");
        const canvas = Object.assign(document.createElement("canvas"), { width: 32, height: 16 });
        const context = canvas.getContext("2d", { willReadFrequently: true });
        const shows = mirror.frameAt();
        Art.drawFrame(context, loaded, 0, 0, 0);                       // the crystal as drawn, on the left
        Art.drawFrame(context, loaded, shows.frame, 16, 0, 16, 16, shows.ops); // and flipped about its center, on the right
        const pixels = (x) => Array.from(context.getImageData(x, 0, 16, 16).data);
        const left = pixels(0), right = pixels(16);
        let mirrored = true;
        for (let y = 0; y < 16 && mirrored; y++) for (let x = 0; x < 16; x++) {
            for (let k = 0; k < 4; k++) if (left[(y * 16 + x) * 4 + k] !== right[(y * 16 + (15 - x)) * 4 + k]) mirrored = false;
        }
        const seen = [];
        for (const at of [0, 399, 400, 799, 800]) { time = at; seen.push(base.frameAt().frame); }
        return { names: loaded.animations.names(), same: loaded === again, sharedSheet: loaded.source === plain.source, plainHas: plain.animations, mirrored, seen };""",
    )
    assert got["names"] == ["base", "glint", "drift", "mirror", "upside_down"]
    assert got["same"] and got["sharedSheet"] and got["plainHas"] is None, "a reference without animations has none; the sheet is shared"
    assert got["mirrored"], "the mirror alias draws the crystal flipped left to right"
    assert got["seen"] == [0, 0, 1, 1, 0]


def go_down_to_the_cellar(page, live_server):
    open_game(page, live_server)
    make_team(page, "Alpha")
    tab(page, "Party")
    page.locator("#party-play").click()
    expect(page.locator("#stage")).to_have_attribute("data-map", "hub")
    tap(page, 8, 6)
    at(page, 8, 6)
    tap(page, 8, 7)
    page.locator("[data-choice='0']").click()
    expect(page.locator("#stage")).to_have_attribute("data-map", "cellar", timeout=SLOW)
    at(page, 1, 1)
    page.wait_for_load_state("networkidle")  # (the Party panel asks about the town after a warp; let it finish before the test ends)


def crystal_pixels(page):
    """The 32 by 32 pixels of the cellar's crystal tile (6, 3) as the stage drew them (the view shows the whole 8 by 5 map, centered)."""
    return page.evaluate(
        """() => {
            const canvas = document.querySelector('#stage-view');
            const left = (canvas.width - 8 * 32) / 2, top = (canvas.height - 5 * 32) / 2;
            const copy = Object.assign(document.createElement('canvas'), { width: 32, height: 32 });
            const context = copy.getContext('2d', { willReadFrequently: true });
            context.drawImage(canvas, left + 6 * 32, top + 3 * 32, 32, 32, 0, 0, 32, 32);
            return Array.from(context.getImageData(0, 0, 32, 32).data);
        }"""
    )


def test_the_cellars_crystal_tile_plays_its_animation_on_the_map(page, live_server):
    go_down_to_the_cellar(page, live_server)
    expect(page.locator("#stage")).to_have_attribute("data-animated", "1")
    first = crystal_pixels(page)
    assert any(first[3::4]), "the crystal was drawn"
    # (the crystal's two frames show for 400 ms each: within a couple of seconds the tile has changed)
    expect(page.locator("#stage-view")).to_be_visible()
    changed = False
    for _ in range(40):
        page.wait_for_timeout(100)
        if crystal_pixels(page) != first:
            changed = True
            break
    assert changed, "the animated tile changed frame without the player doing anything"


def test_with_reduced_motion_the_cellars_crystal_stays_on_its_first_frame(page, live_server):
    page.emulate_media(reduced_motion="reduce")
    go_down_to_the_cellar(page, live_server)
    expect(page.locator("#stage")).to_have_attribute("data-animated", "1")
    first = crystal_pixels(page)
    page.wait_for_timeout(1200)
    assert crystal_pixels(page) == first
