"""Force disuse in a real browser: a hero that holds something retired goes through its scene, over the whole page, before play.
The server's calls are stubbed (its own tests run the dialog); this tests what the page draws and what it sends back."""

import json
import re

from playwright.sync_api import expect

from .test_heroes import make_team, open_game

ACK = {"type": "ack", "accepts": {"choice": [], "next": True, "cancel": False}}
CHOICE = {"type": "choice", "kind": "question", "options": [{"text": "Fighter"}, {"text": "Scout"}], "accepts": {"choice": [0, 1], "next": False, "cancel": True}}
STATUS = {"pending": True, "talking": False, "prompt": None, "restorable": [], "lost": {"job": {"key": "x", "name": "X"}, "abilities": [], "items": []}}


QUIET = {"pending": False, "talking": False, "prompt": None, "restorable": [], "lost": {"job": None, "abilities": [], "items": []}}


class Server:
    """Answers the disuse calls: ``status`` to the read, and each POST (start, then every next) with the next of ``frames``. ``sent`` is what
    the page posted. It says there is nothing to settle until a test sets ``status``, so the game page that team-making returns to is quiet."""

    def __init__(self, page):
        self.status, self.frames, self.sent = QUIET, [], []
        page.route(re.compile(r"/api/heroes/\d+/disuse(/\w+)?$"), self.answer)

    def answer(self, route):
        request = route.request
        if request.method == "GET":
            return route.fulfill(json=self.status)
        self.sent.append((request.url.rsplit("/", 1)[1], json.loads(request.post_data or "{}")))
        return route.fulfill(json=self.frames.pop(0))

    def scene(self, frames, status=STATUS):
        self.status, self.frames = status, list(frames)


def a_hero_in_a_game_page(page, live_server):
    """Mike with a hero on a team, and the stub in place; returns the stub."""
    open_game(page, live_server)
    server = Server(page)
    make_team(page, "Alpha")
    page.wait_for_load_state("networkidle")  # (so reloading does not cancel the game's own calls: the browser would report a failed request)
    return server


def test_a_scene_plays_over_the_page_and_the_answers_go_back(page, live_server):
    server = a_hero_in_a_game_page(page, live_server)
    server.scene([
        {"events": [{"type": "scene", "name": "black"}, {"type": "text", "text": "You are asleep."}], "prompt": ACK, "ended": False, "pending": True},
        {"events": [{"type": "text", "text": "Which class will you be?"}], "prompt": CHOICE, "ended": False, "pending": True},
        {"events": [{"type": "text", "text": "You wake."}], "prompt": None, "ended": True, "pending": False,
         "restored": {"abilities": ["Fire"], "items": [{"name": "Tonic", "qty": 3}]}},
    ])
    page.goto("/play/")
    scene = page.locator("#disuse-scene")
    expect(scene).to_be_visible()
    expect(scene).to_have_attribute("data-scene", "black")
    expect(scene).to_contain_text("You are asleep.")
    page.locator("#disuse-next").click()
    expect(scene).to_contain_text("Which class will you be?")
    page.locator("[data-choice='1']").click()
    expect(scene).to_contain_text("You wake.")
    expect(scene).to_contain_text("Returned to you: Fire, Tonic (x3).")
    expect(page.locator("#disuse-pending")).to_have_count(0)
    page.locator("#disuse-close").click()
    expect(scene).to_have_count(0)
    assert server.sent == [("start", {}), ("next", {}), ("next", {"choice": 1})]


def test_a_scene_that_ends_with_something_still_to_settle_says_so(page, live_server):
    server = a_hero_in_a_game_page(page, live_server)
    server.scene([{"events": [{"type": "scene", "name": "dream"}, {"type": "text", "text": "No class is open to you yet."}], "prompt": None, "ended": True, "pending": True}])
    page.goto("/play/")
    expect(page.locator("#disuse-scene")).to_have_attribute("data-scene", "dream")
    expect(page.locator("#disuse-pending")).to_have_text("Aria still has something to settle. Come back to it later.")
    page.locator("#disuse-close").click()
    expect(page.locator("#disuse-scene")).to_have_count(0)


def test_a_scene_a_reload_interrupted_is_picked_up_where_it_stood(page, live_server):
    server = a_hero_in_a_game_page(page, live_server)
    server.scene([{"events": [], "prompt": None, "ended": True, "pending": False}], {**STATUS, "talking": True, "prompt": CHOICE})
    page.goto("/play/")
    expect(page.locator("[data-choice]")).to_have_count(2)
    page.locator("[data-choice='0']").click()
    page.locator("#disuse-close").click()
    assert server.sent == [("next", {"choice": 0})], "(no start: the server still has the scene)"


def test_a_hero_with_nothing_to_settle_goes_straight_to_the_game(page, live_server):
    a_hero_in_a_game_page(page, live_server)
    page.goto("/play/")
    expect(page.locator("#who")).to_be_visible()
    expect(page.locator("#disuse-scene")).to_have_count(0)
