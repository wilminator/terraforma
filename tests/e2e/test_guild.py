"""The guild's three screens in a real browser: an NPC dialog reaches `add_team`, `find_party` or `party_requests`, and the talk panel draws the
server's offer. The example game has no guildmaster, so these tests make the server say it is talking to one (the views are the guild routes' own shapes)
and check what the browser sends back."""

from playwright.sync_api import expect

from .test_heroes import make_team, open_game, tab


def guild_talk(page, command):
    """The next talk answers with a guildmaster whose dialog is at `command`."""
    frame = {"talking": True, "npc": {"id": 1, "name": "Guildmaster"}, "events": [{"type": "text", "text": "Welcome to the guild."}], "prompt": {"type": "activity", "command": command, "parts": []}}
    page.route("**/npcs/*/talk", lambda route: route.fulfill(json=frame))


def talk(page):
    tab(page, "Nearby")
    page.locator("#nearby-action").select_option("talk")
    page.get_by_role("button", name="Refresh").click()
    page.get_by_role("button", name="Talk to Old Tom").click()
    expect(page.locator("#guild")).to_be_visible()


def start(page, live_server, command, view):
    open_game(page, live_server)
    make_team(page, "Alpha")
    page.route("**/dialog/guild", lambda route: route.fulfill(json=view) if route.request.method == "GET" else route.fallback())
    guild_talk(page, command)
    talk(page)


def test_a_team_is_added_to_the_party_and_the_dialog_goes_on(page, live_server):
    start(page, live_server, "add_team", {"command": "add_team", "teams": [{"id": 7, "name": "Bravo"}]})
    posted = []
    after = {"talking": True, "npc": {"id": 1, "name": "Guildmaster"}, "events": [{"type": "text", "text": "Bravo joins you."}], "prompt": {"type": "ack"}}
    page.route("**/dialog/guild/add-team", lambda route: (posted.append(route.request.post_data_json), route.fulfill(json={"result": {}, "dialog": after}))[1])
    expect(page.locator("#guild h3")).to_have_text("Add one of your teams to your party")
    page.get_by_role("button", name="Add Bravo").click()
    expect(page.locator(".transcript")).to_contain_text("Bravo joins you.")
    assert posted == [{"team_id": 7}]


def test_no_team_to_add_says_so_and_done_goes_on(page, live_server):
    start(page, live_server, "add_team", {"command": "add_team", "teams": []})
    expect(page.locator("#guild-none")).to_be_visible()
    page.route("**/dialog/next", lambda route: route.fulfill(json={"talking": True, "npc": {"id": 1, "name": "Guildmaster"}, "events": [{"type": "text", "text": "Another time."}], "prompt": None, "ended": True}))
    page.locator("#guild-done").click()
    expect(page.locator(".transcript")).to_contain_text("Another time.")


def test_a_party_is_asked_and_hidden_teams_keep_their_privacy(page, live_server):
    parties = [{"id": 3, "heroes": 2, "teams": [{"name": "Delta"}, {"name": None}]}]
    start(page, live_server, "find_party", {"command": "find_party", "parties": parties})
    row = page.locator("[data-party='3']")
    expect(row).to_contain_text("Delta")
    expect(row).to_contain_text("a team that keeps its name private")
    posted = []
    after = {"talking": True, "npc": {"id": 1, "name": "Guildmaster"}, "events": [{"type": "text", "text": "Your request is sent."}], "prompt": {"type": "ack"}}
    page.route("**/dialog/guild/ask", lambda route: (posted.append(route.request.post_data_json), route.fulfill(json={"result": {}, "dialog": after}))[1])
    row.get_by_role("button", name="Ask to join party 3").click()
    expect(page.locator(".transcript")).to_contain_text("Your request is sent.")
    assert posted == [{"party_id": 3}]


def test_a_leader_accepts_and_declines_requests_and_the_screen_stays(page, live_server):
    requests = {"command": "party_requests", "requests": [{"id": 5, "team": "Echo", "heroes": 1}, {"id": 6, "team": "Foxtrot", "heroes": 3}]}
    start(page, live_server, "party_requests", requests)
    posted = []
    page.route("**/dialog/guild/answer", lambda route: (posted.append(route.request.post_data_json), route.fulfill(json={}))[1])
    page.route("**/dialog/guild", lambda route: route.fulfill(json={"command": "party_requests", "requests": [requests["requests"][1]]}))
    page.get_by_role("button", name="Accept Echo").click()
    expect(page.locator("#guild-note")).to_have_text("Echo joined your party.")
    expect(page.locator("[data-request='5']")).to_have_count(0)
    page.get_by_role("button", name="Decline Foxtrot").click()
    expect(page.locator("#guild-note")).to_have_text("Foxtrot was turned down.")
    assert posted == [{"request_id": 5, "accept": True}, {"request_id": 6, "accept": False}]
    expect(page.locator("#guild")).to_be_visible()  # answering does not move the conversation on


def test_a_refusal_shows_in_the_servers_words(page, live_server, console):
    console.allow("status of 409")
    start(page, live_server, "add_team", {"command": "add_team", "teams": [{"id": 7, "name": "Bravo"}]})
    page.route("**/dialog/guild/add-team", lambda route: route.fulfill(status=409, json={"detail": "that team is in a party"}))
    page.get_by_role("button", name="Add Bravo").click()
    expect(page.locator("#panel-dialog .problem")).to_have_text("that team is in a party")
