"""The admin page in a real browser: events (write a draft, rewrite, publish, judge an entry, cancel), monsters (write, copy, rewrite, retire,
restore) and ending a slot's quest. The calls behind them are tests/test_manual_quests.py, tests/test_monster_authoring.py and
tests/test_rotating_quests.py; here the page must say and do the right thing with them."""

import re

from playwright.sync_api import expect
from sqlalchemy import update

from terraforma.models import Account

from .test_alliances import make_team, second_player
from .test_smoke import log_in

OPENED = "2024-01-01T10:00"
CLOSES = "2028-01-01T10:00"
ENDS = "2028-06-01T10:00"


def admin_page(page, live_server, username="Mike"):
    """An admin (made so on the server) who has logged in and opened the admin page."""
    live_server.make_account(username)

    async def promote(db):
        await db.execute(update(Account).where(Account.username == username).values(is_admin=True))

    live_server.in_db(promote)
    page.goto("/admin/")
    log_in(page, username)
    expect(page).to_have_url("/admin/")
    expect(page.get_by_role("heading", name="Admin", exact=True)).to_be_visible()
    return page


def fill_event(page, key, name, **more):
    form = page.locator("#event-form")
    form.get_by_label("Key (used by dialogs and the quest record)").fill(key)
    form.get_by_label("Name", exact=True).fill(name)
    form.get_by_label("Opens", exact=True).fill(more.get("opens", OPENED))
    form.get_by_label("Entries close").fill(more.get("closes", CLOSES))
    form.get_by_label("Over at").fill(more.get("ends", ENDS))
    if "spec" in more:
        form.get_by_label("The game's description (JSON, optional)").fill(more["spec"])
    return form


def tab(page, name):
    page.get_by_role("button", name=name, exact=True).click()


def test_a_logged_out_visitor_is_sent_to_the_login_and_back(page, live_server):
    live_server.make_account("Mike")
    page.goto("/admin/")
    expect(page).to_have_url("/account/?next=admin")


def test_a_player_who_is_no_admin_is_told_the_page_is_not_for_them(page, live_server, console):
    console.allow("403")
    live_server.make_account("Mike")
    page.goto("/admin/")
    log_in(page, "Mike")
    expect(page.locator("#admin-refused")).to_have_text("This page is for administrators only.")
    expect(page.locator("#admin-events")).to_have_count(0)


def test_an_admin_writes_a_draft_then_rewrites_and_publishes_it(page, live_server):
    admin_page(page, live_server)
    expect(page.locator("#event-none")).to_be_visible()
    page.locator("#event-new").click()
    fill_event(page, "arena-1", "Arena", spec='{"reward": 5}').get_by_role("button", name="Save draft").click()
    row = page.locator("[data-event='arena-1']")
    expect(row).to_contain_text("Arena")
    expect(row).to_have_attribute("data-stage", "draft")

    page.get_by_role("button", name="Edit Arena").click()
    form = page.locator("#event-form")
    expect(form.get_by_label("The game's description (JSON, optional)")).to_have_value('{\n  "reward": 5\n}')
    form.get_by_label("Name", exact=True).fill("Grand Arena")
    form.get_by_role("button", name="Save draft").click()
    expect(row).to_contain_text("Grand Arena")

    page.get_by_role("button", name="Publish Grand Arena").click()
    expect(row).to_have_attribute("data-stage", "published")
    expect(page.get_by_role("button", name="Edit Grand Arena")).to_have_count(0)  # (a published event is cancelled, not rewritten)

    tab(page, "Drafts")
    expect(page.locator("#event-none")).to_be_visible()
    tab(page, "Published")
    expect(page.locator("[data-event='arena-1']")).to_be_visible()


def test_a_field_the_admin_got_wrong_is_explained_and_nothing_is_saved(page, live_server, console):
    console.allow("422")
    admin_page(page, live_server)
    page.locator("#event-new").click()
    form = fill_event(page, "arena-1", "Arena", spec="[1, 2]")
    form.get_by_role("button", name="Save draft").click()
    expect(form.locator(".problem")).to_contain_text("must be a JSON object")
    form.get_by_label("The game's description (JSON, optional)").fill("")
    form.get_by_label("Seconds a team has once it enters (0 for none)").fill("soon")
    form.get_by_role("button", name="Save draft").click()
    expect(form.locator(".problem")).to_contain_text("must be a whole number")
    form.get_by_label("Seconds a team has once it enters (0 for none)").fill("0")
    form.get_by_label("Over at").fill("2025-01-01T10:00")  # (ends before it closes: the server's words come through)
    form.get_by_role("button", name="Save draft").click()
    expect(form.locator(".problem")).to_contain_text("closes no later than it ends")
    page.locator("#event-back").click()
    expect(page.locator("#event-none")).to_be_visible()


def test_an_admin_judges_a_teams_entry_and_sees_the_result(page, new_page, live_server):
    admin_page(page, live_server)
    page.locator("#event-new").click()
    fill_event(page, "arena-1", "Arena").get_by_role("button", name="Save draft").click()
    page.get_by_role("button", name="Publish Arena").click()
    expect(page.locator("[data-event='arena-1']")).to_have_attribute("data-stage", "published")

    player_page, calls = second_player(new_page, live_server, "Zed")
    team = make_team(calls, "Gladiators")
    event = calls.page.request.get("/api/teams/%d/manual-quests" % team["id"]).json()["events"][0]
    answer = player_page.request.post(f"/api/teams/{team['id']}/manual-quests/{event['id']}/enter", headers=calls.csrf)
    assert answer.ok, answer.text()

    page.get_by_role("button", name="Open Arena").click()
    expect(page.locator("#event-detail")).to_contain_text("1 entered")
    entry = page.locator("[data-entry='Gladiators']")
    expect(entry).to_have_attribute("data-status", "active")
    entry.get_by_label("Score").fill("90")
    entry.get_by_role("button", name="Judge Gladiators as winners").click()
    expect(page.locator("[data-entry='Gladiators']")).to_have_attribute("data-status", "completed")
    expect(page.locator("[data-entry='Gladiators']")).to_contain_text("score 90")
    expect(page.locator("[data-entry='Gladiators']").get_by_role("button")).to_have_count(0)  # (judged: nothing more to do)


def test_cancelling_a_published_event_asks_twice(page, live_server):
    admin_page(page, live_server)
    page.locator("#event-new").click()
    fill_event(page, "arena-1", "Arena").get_by_role("button", name="Save draft").click()
    page.get_by_role("button", name="Publish Arena").click()
    row = page.locator("[data-event='arena-1']")
    page.locator("#event-cancel-1").click()
    expect(row).to_have_attribute("data-stage", "published")  # (the first press only asks)
    page.get_by_role("button", name="Yes, cancel Arena").click()
    expect(row).to_have_attribute("data-stage", "cancelled")
    expect(row.get_by_role("button", name="Open Arena")).to_be_visible()


def test_an_admin_writes_a_monster_and_copies_a_seed_monster(page, live_server, console):
    console.allow("422")
    admin_page(page, live_server)
    tab(page, "Monsters")
    slime = page.locator("[data-monster='slime']")
    expect(slime).to_have_attribute("data-authored", "false")
    expect(slime).to_contain_text("from the seed")
    expect(slime.get_by_role("button", name="Edit Slime")).to_have_count(0)  # (the seed changes it, not this page)

    page.locator("#monster-new").click()
    form = page.locator("#monster-form")
    form.get_by_label("Key (cannot change once written)").fill("ember-drake")
    form.get_by_label("Name", exact=True).fill("Ember Drake")
    form.get_by_label("Personality (its look)").fill("nosuchlook")
    form.get_by_label("Stats, one per line (Strength: 12)").fill("HP: 90\nStrength: 12")
    form.get_by_role("button", name="Save monster").click()
    expect(form.locator(".problem")).to_contain_text("nosuchlook")  # (the server names what is wrong and nothing is written)
    form.get_by_label("Personality (its look)").fill("slime")
    form.get_by_label("Stats, one per line (Strength: 12)").fill("HP: ninety")
    form.get_by_role("button", name="Save monster").click()
    expect(form.locator(".problem")).to_contain_text("Each line must be a name, a colon and a whole number")
    form.get_by_label("Stats, one per line (Strength: 12)").fill("HP: 90\nStrength: 12")
    form.get_by_role("button", name="Save monster").click()

    drake = page.locator("[data-monster='ember-drake']")
    expect(drake).to_have_attribute("data-authored", "true")
    expect(drake).to_contain_text("written by an admin")
    expect(drake).to_contain_text("in play")

    slime.get_by_role("button", name="Copy Slime as a new monster").click()
    copy = page.locator("#monster-form")
    expect(copy.get_by_label("Name", exact=True)).to_have_value("Slime")
    expect(copy.get_by_label("Key (cannot change once written)")).to_have_value("")
    copy.get_by_label("Key (cannot change once written)").fill("big-slime")
    copy.get_by_label("Name", exact=True).fill("Big Slime")
    copy.get_by_role("button", name="Save monster").click()
    expect(page.locator("[data-monster='big-slime']")).to_have_attribute("data-authored", "true")


def test_an_admin_rewrites_retires_and_restores_a_monster(page, live_server):
    admin_page(page, live_server)
    tab(page, "Monsters")
    page.locator("#monster-new").click()
    form = page.locator("#monster-form")
    form.get_by_label("Key (cannot change once written)").fill("ember-drake")
    form.get_by_label("Name", exact=True).fill("Ember Drake")
    form.get_by_label("Personality (its look)").fill("slime")
    form.get_by_label("Stats, one per line (Strength: 12)").fill("HP: 90")
    form.get_by_role("button", name="Save monster").click()

    drake = page.locator("[data-monster='ember-drake']")
    page.get_by_role("button", name="Edit Ember Drake").click()
    form = page.locator("#monster-form")
    expect(form.get_by_label("Key (cannot change once written)")).to_have_js_property("readOnly", True)
    expect(form.get_by_label("Stats, one per line (Strength: 12)")).to_have_value(re.compile(r"^HP: 90\n"))  # (the server filled in the other stats)
    form.get_by_label("Name", exact=True).fill("Cinder Drake")
    form.get_by_role("button", name="Save monster").click()
    expect(drake).to_contain_text("Cinder Drake")

    page.get_by_role("button", name="Retire Cinder Drake").click()
    expect(drake).to_have_attribute("data-active", "false")
    expect(drake).to_contain_text("retired")
    tab(page, "Retired")
    expect(drake).to_be_visible()
    expect(page.locator("[data-monster='slime']")).to_have_count(0)
    page.get_by_role("button", name="Restore Cinder Drake").click()
    expect(page.locator("#monster-none")).to_be_visible()  # (restored, so no longer in the retired list)
    tab(page, "Written by admins")
    expect(drake).to_have_attribute("data-active", "true")


def test_ending_a_slots_quest_asks_twice_and_says_what_the_server_says(page, live_server, console):
    console.allow("404")
    admin_page(page, live_server)
    tab(page, "Quest slots")
    page.locator("#slot-end").click()
    page.get_by_role("button", name="Yes, end it now").click()
    expect(page.locator("#slots-problem")).to_have_text("Type the slot's key first.")
    page.get_by_label("Slot key").fill("nowhere")
    page.locator("#slot-end").click()
    page.get_by_role("button", name="Yes, end it now").click()
    expect(page.locator("#slots-problem")).to_contain_text("no such quest slot")
