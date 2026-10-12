"""The fight on the stage, in a real browser. The plumbing comes first: the fight model follows the server's events to the numbers the
server stores, the fight link opens, retries and gives up, and a walk into the marsh (or a reload in the middle of a fight) opens the fight
on the one page. Then the playback: the rounds the server plays arrive over the fight socket and are shown event by event, can be sped up
and skipped, and a player who asks for less motion sees no movement. Commands are given through the call here (the command menu is the
next slice); the example's heroes fight a slime in the marsh of the hub."""

import pytest
from playwright.sync_api import expect
from sqlalchemy import select

from terraforma.models import Map

from .test_map import SLOW, play, tap

FOLLOW_ROUND = """async (events) => {
  const { FightModel } = await import('/client/js/fight-model.js');
  const view = { id: 1, round: 1, over: false, vital: 'HP', deadline: null, fighters: [
    { party: 0, group: 0, character: 0, name: 'Aria', alive: true, fled: false, resources: { HP: [20, 20], MP: [5, 5] }, statuses: [], yours: true, committed: false },
    { party: 1, group: 0, character: 0, name: 'Slime', alive: true, fled: false, resources: { HP: [14, 14], MP: [0, 0] }, statuses: [], yours: false, committed: false },
  ] };
  const model = new FightModel(view);
  const said = events.map((event) => model.apply(event));
  const state = Object.fromEntries([...model.fighters].map(([key, each]) => [key, { hp: each.resources.HP, mp: each.resources.MP, alive: each.alive, fled: each.fled, statuses: each.statuses }]));
  return { said: said.map((each) => each.text), marks: said.map((each) => each.marks), reload: said.map((each) => each.reload), state, over: model.over };
}"""


async def sure_encounters(db):
    """The marsh always meets monsters (a walk into it is a fight), so a test need not walk back and forth."""
    hub = await db.scalar(select(Map).where(Map.name == "hub"))
    hub.tileset = [{**kind, "encounter_rate": 10000 if kind["name"].endswith("swamp") else kind.get("encounter_rate", 0)} for kind in hub.tileset]


def into_the_marsh(page, live_server):
    """A team plays and walks into the marsh at (1, 7): the fight opens on the stage. Returns the fight's number."""
    play(page, live_server)
    live_server.in_db(sure_encounters)
    page.evaluate("window.__sameDocument = true")
    tap(page, 1, 7)
    expect(page.locator("#stage")).to_have_attribute("data-scene", "fight", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-link", "open", timeout=SLOW)
    return int(page.locator("#stage").get_attribute("data-fight"))


def faster(page):
    """The fastest speed (four times): the playback is paced for reading, and a test need not wait for that."""
    for _ in range(2):
        page.locator("#fight-speed").click()
    expect(page.locator("#stage")).to_have_attribute("data-speed", "4")


def view_of(page, fight_id):
    return page.request.get(f"/api/fights/{fight_id}").json()


def command(page, fight_id):
    """The player's first hero that stands attacks the first monster that stands, through the call."""
    view = view_of(page, fight_id)
    hero = next(each for each in view["fighters"] if each["yours"] and each["alive"])
    foe = next(each for each in view["fighters"] if each["party"] != hero["party"] and each["alive"])
    token = page.request.get("/api/session").json()["csrf_token"]
    answer = page.request.post(
        f"/api/fights/{fight_id}/commands",
        data={
            "fighter": {"party": hero["party"], "group": hero["group"], "character": hero["character"]},
            "command": "attack_left",
            "target": {"party": foe["party"], "group": foe["group"], "character": foe["character"]},
        },
        headers={"X-CSRF-Token": token},
    )
    assert answer.status == 202, answer.text()
    return answer.json()


def shown_gauges(page):
    """Every card's gauge as the stage shows it: {address: (hp, hp max, alive)}."""
    return page.evaluate(
        """() => Object.fromEntries([...document.querySelectorAll('.fighter')].map((card) =>
            [card.dataset.fighter, [Number(card.dataset.hp), Number(card.dataset.hpMax), card.dataset.alive === 'true']]))"""
    )


def stored_gauges(page, fight_id):
    return {
        f"{each['party']}.{each['group']}.{each['character']}": [each["resources"]["HP"][0], each["resources"]["HP"][1], each["alive"] and not each["fled"]]
        for each in view_of(page, fight_id)["fighters"]
    }


def watch_motion(page):
    """Counts the floating numbers the scene adds (a hit, a heal, a miss), which are its movement besides the fighters' own."""
    page.evaluate(
        """() => { window.__floats = 0;
          new MutationObserver((list) => { for (const each of list) for (const node of each.addedNodes) if (node.classList?.contains('fight-float')) window.__floats += 1; })
            .observe(document.body, { childList: true, subtree: true }); }"""
    )


def settled(page):
    expect(page.locator("#stage")).to_have_attribute("data-playing", "false", timeout=SLOW)


def play_round(page, fight):
    """Commands the round and waits until the stage has shown all of it (the stage's round goes up when the last event is played)."""
    stage = page.locator("#stage")
    before = int(stage.get_attribute("data-round"))
    command(page, fight)
    expect(stage).to_have_attribute("data-round", str(before + 1), timeout=SLOW)
    settled(page)


# --- the plumbing: the model, the link ---------------------------------------------------------------------------------

def test_the_model_follows_a_rounds_events_to_the_numbers_the_server_stores(page, live_server):
    page.goto("/")
    events = [
        ["Turn", [0, 0, 0]], ["Attack", [None, "left", 0]], ["Damage", [1, 0, 0, 6, False]],
        ["Turn", [1, 0, 0]], ["Attack", [None, "left", 0]], ["Miss", [0, 0, 0]],
        ["Turn", [0, 0, 0]], ["Attack", [None, "left", 0]], ["Damage", [1, 0, 0, 20, True]], ["Died", [1, 0, 0, 20, 12]],
        ["AlterStat", [0, 0, 0, "MP", -2]], ["Restore", [0, 0, 0, "HP", 9]], ["RoundEnd", []], ["Tick", []],
    ]
    out = page.evaluate(FOLLOW_ROUND, events)
    assert out["state"]["1.0.0"] == {"hp": [0, 14], "mp": [0, 0], "alive": False, "fled": False, "statuses": []}
    assert out["state"]["0.0.0"]["hp"] == [20, 20] and out["state"]["0.0.0"]["mp"] == [3, 5], "a restore never goes past the maximum"
    texts = [each[0] if each else None for each in out["said"]]
    assert texts == [
        "fight.turn", "fight.attack", "fight.damage", "fight.turn", "fight.attack", "fight.miss",
        "fight.turn", "fight.attack", "fight.critical", "fight.died", None, "fight.restore", None, None,
    ]
    assert out["said"][2][1] == {"name": "Slime", "amount": 6}, "the values the words are made from"
    assert out["marks"][2] == [{"key": "1.0.0", "kind": "damage", "amount": 6}]
    assert out["reload"] == [False] * len(events) and out["over"] is False


def test_a_level_gained_raises_the_maximums_it_names_and_the_pay_out_events_say_their_words(page, live_server):
    page.goto("/")
    out = page.evaluate(FOLLOW_ROUND, [["XpEarned", [0, 0, 0, 5]], ["LevelUp", [0, 0, 0, 2, {"HP": 6, "Strength": 1}]], ["Gold", [0, 0, 3]], ["Drop", [0, 0, 0, "herb", 2]]])
    assert out["state"]["0.0.0"]["hp"] == [20, 26], "the current value stays, the maximum goes up (a stat that is not a resource only counts for the words)"
    assert [each[0] for each in out["said"]] == ["fight.xp", "fight.level", "fight.gold", "fight.drop"]
    assert out["said"][3][1] == {"name": "Aria", "item": "herb", "quantity": 2}


def test_a_party_joining_asks_for_a_fresh_view_and_the_end_of_the_fight_is_known(page, live_server):
    page.goto("/")
    out = page.evaluate(FOLLOW_ROUND, [["PartyJoined", [2, {}, {}]], ["Fled", [0, 0, 0, "flee"]], ["FightOver", []]])
    assert out["reload"] == [True, False, False]
    assert out["state"]["0.0.0"]["fled"] is True and out["over"] is True


def test_a_round_ending_makes_every_fighter_choose_again(page, live_server):
    page.goto("/")
    out = page.evaluate(
        """async () => {
          const { FightModel } = await import('/client/js/fight-model.js');
          const model = new FightModel({ id: 1, round: 3, over: false, deadline: null, fighters: [
            { party: 0, group: 0, character: 0, name: 'Aria', alive: true, fled: false, resources: { HP: [1, 1] }, statuses: [], yours: true, committed: true }] });
          model.endRound(3, false, 'later');
          return [model.round, model.fighter('0.0.0').committed, model.deadline, model.vital];
        }"""
    )
    assert out == [4, False, "later", "HP"]


LINK = """async (steps) => {
  const { FightLink } = await import('/client/js/fight-link.js');
  const sockets = [], seen = [], states = [];
  const link = new FightLink(7, { message: (m) => seen.push(m.type), state: (s) => states.push(s) }, {
    socket: (url) => { const socket = { url, closed: false, close() { this.closed = true; } }; sockets.push(socket); return socket; }, retryMs: 5 });
  link.start();
  const wait = (ms) => new Promise((done) => setTimeout(done, ms));
  for (const step of steps) {
    const socket = sockets[sockets.length - 1];
    if (step === 'joined') socket.onmessage({ data: JSON.stringify({ type: 'joined' }) });
    else if (step === 'round') socket.onmessage({ data: JSON.stringify({ type: 'round' }) });
    else if (step === 'noise') socket.onmessage({ data: 'not json' });
    else if (step === 'drop') socket.onclose();
    else if (step === 'wait') await wait(80);
    else if (step === 'stop') link.stop();
  }
  return { urls: sockets.map((s) => s.url), seen, states, last: link.state, closed: sockets.map((s) => s.closed) };
}"""


def test_the_link_opens_on_the_fights_socket_ignores_what_is_not_ours_and_closes(page, live_server):
    page.goto("/")
    out = page.evaluate(LINK, ["joined", "noise", "round", "stop"])
    assert out["urls"] == [live_server.url.replace("http", "ws") + "/ws/fights/7"]
    assert out["seen"] == ["joined", "round"] and out["states"] == ["open", "closed"] and out["closed"] == [True]


def test_a_dropped_link_tries_again_and_says_when_it_is_open_again(page, live_server):
    page.goto("/")
    out = page.evaluate(LINK, ["joined", "drop", "wait", "joined"])
    assert out["states"] == ["open", "retrying", "open"] and len(out["urls"]) == 2 and out["last"] == "open"


def test_a_link_that_cannot_get_back_gives_up_and_says_so(page, live_server):
    page.goto("/")
    out = page.evaluate(LINK, ["drop", "wait"] * 8)
    assert out["last"] == "lost" and len(out["urls"]) == 6, "the first try and five more"


# --- opening the fight on the one page ---------------------------------------------------------------------------------

def test_a_walk_into_the_marsh_opens_the_fight_on_the_same_page(page, live_server):
    fight = into_the_marsh(page, live_server)
    stage = page.locator("#stage")
    expect(stage).to_have_attribute("data-fight", str(fight))
    expect(page.locator("#fight-layer")).to_be_visible()
    expect(page.locator("#stage-view")).to_be_hidden()
    expect(page.locator("#drawer")).to_be_hidden()  # (a panel over the stage's right side would cover half the fight)
    expect(page.locator(".fighter[data-side='left']")).to_have_count(1)
    expect(page.locator(".fighter[data-side='right']")).not_to_have_count(0)
    expect(page.locator(".fighter[data-side='left'] .fighter-name")).to_have_text("Aria")
    expect(page.locator(".fighter[data-side='right'] .fighter-name").first).to_have_text("Slime")
    expect(page.locator("#fight-round")).to_have_text("Round 1")
    expect(stage).to_have_attribute("data-playing", "false")
    assert page.evaluate("window.__sameDocument") is True, "the page was never reloaded or left"
    assert shown_gauges(page) == stored_gauges(page, fight), "the cards show the fight as the server has it"


def test_a_reload_in_the_middle_of_a_fight_goes_straight_back_to_it(page, live_server):
    fight = into_the_marsh(page, live_server)
    page.reload()
    expect(page.locator("#stage")).to_have_attribute("data-scene", "fight", timeout=SLOW)
    expect(page.locator("#stage")).to_have_attribute("data-fight", str(fight))
    expect(page.locator("#stage")).to_have_attribute("data-link", "open", timeout=SLOW)
    assert shown_gauges(page) == stored_gauges(page, fight)


def test_the_map_ignores_taps_and_keys_while_the_fight_has_the_stage(page, live_server):
    into_the_marsh(page, live_server)
    page.locator("#stage").focus()
    page.keyboard.press("ArrowRight")
    page.locator("#fight-layer").click(position={"x": 100, "y": 100})
    expect(page.locator("#stage")).to_have_attribute("data-walking", "false")
    expect(page.locator("#stage")).to_have_attribute("data-scene", "fight")


# --- playback ----------------------------------------------------------------------------------------------------------

def test_a_round_the_server_plays_is_shown_event_by_event_and_the_gauges_end_where_the_server_has_them(page, live_server):
    fight = into_the_marsh(page, live_server)
    faster(page)
    watch_motion(page)
    play_round(page, fight)
    shown = int(page.locator("#stage").get_attribute("data-shown"))
    assert shown >= 3, "a turn, an attack and what came of it, at least"
    lines = page.locator("#fight-log li")
    assert lines.count() >= 2
    expect(page.locator("#fight-log")).to_contain_text("Aria")
    expect(page.locator("#fight-round")).to_have_text("Round 2")
    assert page.evaluate("window.__floats") > 0, "a hit or a miss floats up"
    assert shown_gauges(page) == stored_gauges(page, fight)


def test_the_speed_button_cycles_and_a_round_is_faster_at_four_times(page, live_server):
    into_the_marsh(page, live_server)
    stage = page.locator("#stage")
    expect(stage).to_have_attribute("data-speed", "1")
    page.locator("#fight-speed").click()
    expect(stage).to_have_attribute("data-speed", "2")
    expect(page.locator("#fight-speed")).to_have_text("Speed 2×")
    page.locator("#fight-speed").click()
    expect(stage).to_have_attribute("data-speed", "4")
    page.locator("#fight-speed").click()
    expect(stage).to_have_attribute("data-speed", "1")


def test_skip_shows_the_rest_of_the_round_at_once(page, live_server):
    fight = into_the_marsh(page, live_server)
    expect(page.locator("#fight-skip")).to_be_disabled()  # (nothing is being played)
    command(page, fight)
    expect(page.locator("#stage")).to_have_attribute("data-playing", "true", timeout=SLOW)
    page.locator("#fight-skip").click()
    expect(page.locator("#stage")).to_have_attribute("data-playing", "false", timeout=5000)
    expect(page.locator("#fight-round")).to_have_text("Round 2")
    assert shown_gauges(page) == stored_gauges(page, fight)


def test_a_player_who_asks_for_less_motion_sees_the_same_words_and_gauges_but_nothing_floats(page, live_server):
    page.emulate_media(reduced_motion="reduce")
    fight = into_the_marsh(page, live_server)
    faster(page)
    watch_motion(page)
    play_round(page, fight)
    expect(page.locator("#fight-log")).to_contain_text("Aria")
    assert page.evaluate("window.__floats") == 0
    assert shown_gauges(page) == stored_gauges(page, fight)


def test_a_fight_played_to_its_end_says_so_and_the_player_leaves_it_for_the_map(page, live_server):
    fight = into_the_marsh(page, live_server)
    faster(page)
    stage = page.locator("#stage")
    for _ in range(60):
        if view_of(page, fight)["over"]:
            break
        play_round(page, fight)
    else:
        pytest.fail("the fight never ended")
    expect(stage).to_have_attribute("data-over", "true", timeout=SLOW)
    expect(page.locator("#fight-round")).to_have_text("The fight is over")
    expect(page.locator("#fight-log")).to_contain_text("The fight is over.")
    assert shown_gauges(page) == stored_gauges(page, fight)
    page.locator("#fight-leave").click()
    expect(stage).to_have_attribute("data-scene", "map")
    expect(page.locator("#fight-layer")).to_have_count(0)
    expect(page.locator("#stage-view")).to_be_visible()
    expect(stage).not_to_have_attribute("data-fight", str(fight))
    assert page.evaluate("window.__sameDocument") is True
