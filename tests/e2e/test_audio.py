"""The page's sound in a real browser: the cues and tunes a game registers, the player's choice (sound off, the volumes) kept in the browser,
a tune that waits for the first touch, and the example game's own sounds on the map and in a fight. Most tests hand the module fake audio
elements, so no test depends on whether the browser can decode or may play a file."""

from playwright.sync_api import expect

from .test_fight import into_the_marsh
from .test_heroes import open_game

LOAD = "const A = await import('/client/js/audio.js');"
# Fake audio elements that remember what they were asked, a storage that is a plain object, and a target for the first touch.
FAKES = """
    const made = [];
    const make = (src) => {
        const element = { src, volume: 1, loop: false, paused: false, plays: 0, fail: null,
            play() { this.plays += 1; if (this.fail) return Promise.reject(Object.assign(new Error('no'), { name: this.fail })); return Promise.resolve(); },
            pause() { this.paused = true; } };
        made.push(element);
        return element;
    };
    const kept = {};
    const storage = { getItem: (key) => kept[key] ?? null, setItem: (key, value) => { kept[key] = value; } };
    const target = new EventTarget();
    const url = (file) => '/assets/' + file;
    const sounds = (extra = {}) => new A.Sounds({ url, make, storage, target, ...extra });
    const srcs = () => made.map((each) => each.src);
"""


def run(page, script):
    page.goto("/")
    expect(page.locator("html")).to_have_attribute("data-example-ready", "true")
    return page.evaluate("async () => {" + LOAD + FAKES + script + "}")


def test_the_cues_of_a_fights_marks_are_the_most_specific_first(page):
    got = run(
        page,
        """return {
            act: A.cuesOf({ kind: 'act', cue: 'attack_close' }), plain: A.cuesOf({ kind: 'act' }), hit: A.cuesOf({ kind: 'damage' }),
            heal: A.cuesOf({ kind: 'heal' }), turn: A.cuesOf({ kind: 'turn' }), alter: A.cuesOf({ kind: 'alter' }),
        };""",
    )
    assert got == {
        "act": ["fight.attack_close", "fight.act"], "plain": ["fight.act"], "hit": ["fight.damage"],
        "heal": ["fight.heal"], "turn": [], "alter": [],
    }  # (a turn starting or a stat changing has nothing to hear)


def test_an_effect_plays_the_first_name_the_game_gave_a_file_and_nothing_else_makes_a_sound(page):
    got = run(
        page,
        """const s = sounds();
        const before = [s.available, s.play('fight.damage')];
        s.add({ 'fight.act': 'sfx/attack.mp3', 'fight.damage': 'sfx/hit.mp3' });
        return {
            before, available: s.available,
            specific: s.play('fight.attack_close', 'fight.act'), exact: s.play('fight.damage'), none: s.play('fight.heal'),
            srcs: srcs(), plays: made.map((each) => each.plays),
        };""",
    )
    assert got["before"] == [False, None]  # (a game with no sound is silent and has no sound button)
    assert got["available"] is True
    assert (got["specific"], got["exact"], got["none"]) == ("fight.act", "fight.damage", None)
    assert got["srcs"] == ["/assets/sfx/attack.mp3", "/assets/sfx/hit.mp3"] and got["plays"] == [1, 1]


def test_a_tune_loops_keeps_playing_for_the_same_scene_and_gives_way_to_the_next(page):
    got = run(
        page,
        """const s = sounds();
        s.addMusic({ map: 'music/map.mp3', fight: 'music/combat.mp3' });
        s.music('map'); s.music('map');
        const one = { made: made.length, loop: made[0].loop, plays: made[0].plays };
        s.music('fight');
        const two = { made: made.length, firstStopped: made[0].paused, secondPlays: made[1].plays };
        s.music(null);
        return { one, two, stopped: made[1].paused };""",
    )
    assert got["one"] == {"made": 1, "loop": True, "plays": 1}  # (asking again for the tune that plays does not start it over)
    assert got["two"] == {"made": 2, "firstStopped": True, "secondPlays": 1}
    assert got["stopped"] is True


def test_a_tune_the_browser_will_not_start_yet_starts_at_the_first_touch(page):
    got = run(
        page,
        """let first = true;
        const refusing = (src) => { const e = make(src); if (first) { e.fail = 'NotAllowedError'; first = false; } return e; };
        const s = new A.Sounds({ url, make: refusing, storage, target });
        s.addMusic({ map: 'music/map.mp3' });
        s.music('map');
        await Promise.resolve(); await Promise.resolve();
        const waiting = made.length;
        target.dispatchEvent(new Event('pointerdown'));
        const afterTouch = made.length;
        target.dispatchEvent(new Event('keydown'));
        return { waiting, afterTouch, again: made.length, plays: made.map((each) => each.plays) };""",
    )
    assert got["waiting"] == 1 and got["afterTouch"] == 2  # (the refused try, then one more when the page is touched)
    assert got["again"] == 2 and got["plays"] == [1, 1]  # (a later touch starts nothing: the listeners are gone)


def test_a_file_the_browser_cannot_play_is_left_alone_and_not_tried_at_every_touch(page):
    got = run(
        page,
        """const broken = (src) => { const e = make(src); e.fail = 'NotSupportedError'; return e; };
        const s = new A.Sounds({ url, make: broken, storage, target });
        s.addMusic({ map: 'music/map.mp3' });
        s.music('map');
        await Promise.resolve(); await Promise.resolve();
        target.dispatchEvent(new Event('pointerdown'));
        return made.length;""",
    )
    assert got == 1


def test_sound_off_silences_effects_and_stops_the_tune_and_sound_on_brings_the_tune_back(page):
    got = run(
        page,
        """const s = sounds();
        s.add({ 'panel.open': 'sfx/open.mp3' }); s.addMusic({ map: 'music/map.mp3' });
        s.music('map');
        s.set({ muted: true });
        const muted = { effect: s.play('panel.open'), tuneStopped: made[0].paused };
        s.set({ muted: false });
        return { muted, back: made.length, effect: s.play('panel.open'), playing: !made[made.length - 1].paused };""",
    )
    assert got["muted"] == {"effect": None, "tuneStopped": True}
    assert got["back"] == 2 and got["effect"] == "panel.open" and got["playing"] is True


def test_the_volumes_are_kept_between_zero_and_one_and_a_tune_follows_its_volume(page):
    got = run(
        page,
        """const s = sounds();
        s.add({ 'panel.open': 'sfx/open.mp3' }); s.addMusic({ map: 'music/map.mp3' });
        s.music('map');
        s.set({ music: 0.25, effects: 7 });
        const mid = { music: s.music_volume, effects: s.effects, tune: made[0].volume };
        s.play('panel.open');
        s.set({ effects: 0 });
        return { mid, effect: made[1].volume, silent: s.play('panel.open'), tune: made[0].paused };""",
    )
    assert got["mid"] == {"music": 0.25, "effects": 1, "tune": 0.25}  # (a volume past one is one)
    assert got["effect"] == 1 and got["silent"] is None and got["tune"] is False


def test_the_choice_is_kept_in_the_browser_and_read_back_and_refused_storage_is_no_trouble(page):
    got = run(
        page,
        """const s = sounds();
        s.set({ muted: true, effects: 0.5, music: 0.75 });
        const t = sounds();
        const never = { getItem() { throw new Error('refused'); }, setItem() { throw new Error('refused'); } };
        const u = new A.Sounds({ url, make, storage: never, target });
        u.set({ muted: true });
        return { read: [t.muted, t.effects, t.music_volume], refused: u.muted, fresh: new A.Sounds({ url, make, storage: { getItem: () => 'not json', setItem() {} }, target }).muted };""",
    )
    assert got == {"read": [True, 0.5, 0.75], "refused": True, "fresh": False}


def test_a_listener_hears_the_choice_and_the_files_change(page):
    got = run(
        page,
        """const s = sounds(); let heard = 0;
        const stop = s.onChange(() => { heard += 1; });
        s.add({ a: 'a.mp3' }); s.set({ muted: true }); stop(); s.set({ muted: false });
        return heard;""",
    )
    assert got == 2


def test_the_example_game_has_a_sound_button_that_turns_the_sound_off_and_stays_off(page, live_server):
    open_game(page, live_server)
    button = page.locator("#sound")
    expect(button).to_have_text("Sound on")
    button.click()
    expect(button).to_have_text("Sound off")
    page.reload()
    expect(page.locator("#who")).to_be_visible()
    expect(page.locator("#sound")).to_have_text("Sound off")  # (the choice was kept in the browser)
    page.locator("#sound").click()
    expect(page.locator("#sound")).to_have_text("Sound on")


def test_the_example_game_plays_the_map_tune_and_in_a_fight_the_fight_tune_and_its_start(page, live_server):
    asked = []
    page.on("request", lambda request: asked.append(request.url.split("/assets/")[-1]) if "/assets/audio/" in request.url else None)
    into_the_marsh(page, live_server)
    assert "audio/music/map.mp3" in asked, asked
    assert "audio/music/combat.mp3" in asked and "audio/sfx/fight_start.mp3" in asked, asked


def test_a_game_that_gives_the_page_no_sound_has_no_sound_button(page, live_server):
    page.route("**/example.js", lambda route: route.fulfill(content_type="text/javascript", body="export default function register(shell) { shell.on('ready', () => { document.documentElement.dataset.exampleReady = 'true'; }); }"))
    open_game(page, live_server)
    expect(page.locator("#sound")).to_be_hidden()
