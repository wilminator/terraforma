"""The party panel and the hub's offer of a running fight to join. The server's side (the chance, which fight, the relationships) is tested in
``tests/test_joining.py`` and ``tests/test_towns.py``; here the page's side is: the offer is shown without saying what the fight is, the
leader answers with a button, and the answer is said. The offer calls are answered by the test, so no luck is needed."""

from playwright.sync_api import expect

from .test_heroes import make_team, open_game, tab

QUIET = {"in_town": False, "teams": []}


def team_in_game(page, live_server):
    """A player with a team, back on the game page and settled (the team's page was left with calls still on their way: those would be
    reported as failed requests)."""
    open_game(page, live_server)
    make_team(page, "Alpha")
    expect(page.locator("#who")).to_have_text("Playing as Mike")


def watch_calls(page):
    """Counts the calls on their way, so a test can wait until none is (the browser reports a call cut off by the page closing as a failed
    request, and a call whose answer has come may still be unfinished when the test's last check passes)."""
    pending = set()
    page.on("request", lambda request: pending.add(request))
    page.on("requestfinished", lambda request: pending.discard(request))
    page.on("requestfailed", lambda request: pending.discard(request))
    return pending


def settle(page, pending):
    page.wait_for_timeout(100)  # (the browser's news of a call comes a little after the call)
    for _ in range(100):
        if not pending:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"calls still on their way: {[each.url for each in pending]}")


def with_offer(page, answers):
    """Answers the offer calls: $answers is {"offer": bool, "accept": json, "decline": json}, and the offer stands until it is answered.
    Returns the calls the page made."""
    calls = []

    def offer(route):
        calls.append("get")
        route.fulfill(json={"offer": answers["offer"]})

    def answer(which):
        def handler(route):
            calls.append(which)
            answers["offer"] = False
            route.fulfill(json=answers[which])
        return handler

    page.route("**/town/join-offer", offer)
    page.route("**/town/join-offer/accept", answer("accept"))
    page.route("**/town/join-offer/decline", answer("decline"))
    return calls


def test_an_offer_is_shown_without_saying_what_the_fight_is_and_accepting_it_joins(page, live_server):
    team_in_game(page, live_server)
    pending = watch_calls(page)
    calls = with_offer(page, {"offer": True, "accept": {"joined": True, "fight": None, **QUIET}, "decline": {}})
    tab(page, "Party")
    offer = page.locator("#party-offer")
    expect(offer).to_be_visible()
    expect(offer).to_contain_text("You will not know what is in it")
    page.locator("#party-join").click()
    expect(page.locator("#panel-party .note")).to_have_text("Your party has joined the fight.")
    expect(offer).to_have_count(0)
    settle(page, pending)
    assert calls.count("accept") == 1 and "decline" not in calls


def test_declining_an_offer_goes_on_without_it(page, live_server):
    team_in_game(page, live_server)
    pending = watch_calls(page)
    calls = with_offer(page, {"offer": True, "accept": {}, "decline": {"joined": False, "fight": None, **QUIET}})
    tab(page, "Party")
    expect(page.locator("#party-offer")).to_be_visible()
    page.locator("#party-decline").click()
    expect(page.locator("#panel-party .note")).to_have_text("The party has left town.")
    expect(page.locator("#party-offer")).to_have_count(0)
    settle(page, pending)
    assert calls.count("decline") == 1 and "accept" not in calls


def test_there_is_no_offer_when_the_hub_has_made_none(page, live_server):
    team_in_game(page, live_server)
    pending = watch_calls(page)
    with_offer(page, {"offer": False, "accept": {}, "decline": {}})
    tab(page, "Party")
    expect(page.locator("#party-play")).to_be_visible()
    expect(page.locator("#party-offer")).to_have_count(0)
    settle(page, pending)
