"""Chat in a real browser: saying, hearing, whispering, muting, flagging, and an admin settling a flag."""

from playwright.sync_api import expect
from sqlalchemy import select, update

from terraforma.accounts.service import create_account, set_handle
from terraforma.chat.models import ChatMessage
from terraforma.models import Account

from .conftest import PASSWORD
from .test_heroes import make_team, open_game, tab


def start(page, live_server):
    """Mike, with a handle and a team of one hero, on the Chat panel; Zed has a handle and has said hello to everyone."""
    open_game(page, live_server)

    async def handles(db):
        mike = await db.scalar(select(Account).where(Account.username == "Mike"))
        await set_handle(db, mike, "Mike the Bold")
        zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
        await set_handle(db, zed, "Zed the Fast")
        db.add(ChatMessage(kind="global", key="all", sender_id=zed.id, body="hello everyone"))

    live_server.in_db(handles)
    make_team(page, "Alpha")
    tab(page, "Chat")
    page.locator("#chat-channel").select_option(label="Everyone")


def test_a_player_says_something_and_it_appears_in_the_channel(page, live_server):
    start(page, live_server)
    expect(page.locator("#chat-lines")).to_contain_text("Zed the Fast: hello everyone")
    page.locator("#chat-body").fill("hi there")
    page.get_by_role("button", name="Send").click()
    mine = page.locator("#chat-lines [data-mine='yes']")
    expect(mine).to_have_text("Mike the Bold: hi there")
    expect(page.locator("#chat-body")).to_have_value("")


def test_the_channel_list_has_the_everyone_nearby_and_town_channels(page, live_server):
    start(page, live_server)
    expect(page.locator("#chat-channel option")).to_have_text(["Nearby", "Town", "Everyone"])


def test_a_muted_player_disappears_and_comes_back_when_unmuted(page, live_server):
    start(page, live_server)
    expect(page.locator("#chat-lines")).to_contain_text("hello everyone")
    page.get_by_role("button", name="Mute Zed the Fast").click()
    expect(page.locator("#chat-muted [data-handle='Zed the Fast']")).to_be_visible()
    expect(page.locator("#chat-lines")).not_to_contain_text("hello everyone")
    page.get_by_role("button", name="Unmute Zed the Fast").click()
    expect(page.locator("#chat-muted")).to_contain_text("Nobody is muted.")
    expect(page.locator("#chat-lines")).to_contain_text("hello everyone")


def test_a_whisper_goes_to_a_handle(page, live_server):
    start(page, live_server)
    page.locator("#chat-whisper").fill("Zed the Fast")
    page.get_by_role("button", name="Whisper", exact=True).click()
    expect(page.locator("#chat-channel")).to_have_value("whisper\nZed the Fast")
    page.locator("#chat-body").fill("psst")
    page.get_by_role("button", name="Send").click()
    expect(page.locator("#chat-lines")).to_have_text("Mike the Bold: psst")


def test_a_flag_goes_to_the_admin_who_settles_it(page, live_server):
    start(page, live_server)
    expect(page.locator("#chat-lines")).to_contain_text("hello everyone")
    expect(page.locator("#chat-admin")).to_be_hidden()  # Mike is no admin
    page.get_by_role("button", name="Flag Zed the Fast's message").click()
    page.locator("#chat-flag-reason").select_option("tou")
    page.locator("#chat-flag-note").fill("rude")
    page.locator("#chat-flag-send").click()
    expect(page.locator("#chat-flagged")).to_have_text("Thank you. An admin will look at it.")

    async def promote(db):
        await db.execute(update(Account).where(Account.username == "Mike").values(is_admin=True))

    live_server.in_db(promote)
    tab(page, "Nearby")
    tab(page, "Chat")
    flag = page.locator("#chat-admin-flags [data-flag]")
    expect(flag).to_contain_text("Mike the Bold flagged Zed the Fast: hello everyone (tou)")
    flag.get_by_role("button", name="Mark flag 1 actioned").click()
    expect(page.locator("#chat-admin-flags")).to_contain_text("No open flags.")


def test_an_admin_bars_a_flagged_player_for_a_day_and_lifts_the_ban(page, live_server):
    start(page, live_server)

    async def promote(db):
        await db.execute(update(Account).where(Account.username == "Mike").values(is_admin=True))

    live_server.in_db(promote)
    page.get_by_role("button", name="Flag Zed the Fast's message").click()
    page.locator("#chat-flag-send").click()
    expect(page.locator("#chat-flagged")).to_be_visible()
    tab(page, "Nearby")
    tab(page, "Chat")
    page.get_by_role("button", name="Bar Zed the Fast from chat for 24 hours").click()
    expect(page.locator("#chat-admin-bans [data-ban='Zed the Fast']")).to_contain_text("Zed the Fast until")
    page.get_by_role("button", name="Lift Zed the Fast's ban").click()
    expect(page.locator("#chat-admin-bans")).to_contain_text("Nobody is barred.")


def test_a_party_channel_offers_a_voice_link_when_voice_is_on(page, live_server, monkeypatch):
    from terraforma.parties import service as parties
    from terraforma.voice import discord

    from ..test_voice import FakeDiscord

    monkeypatch.setattr(discord, "send", FakeDiscord())
    live_server.app.state.settings = live_server.app.state.settings.model_copy(update={"discord_bot_token": "t" * 40, "discord_guild_id": "42"})
    start(page, live_server)

    async def party(db):
        await parties.create_party(db, 1, 20)

    live_server.in_db(party)
    tab(page, "Nearby")
    tab(page, "Chat")
    expect(page.locator("#chat-voice")).to_be_hidden()  # the Everyone channel has no room
    page.locator("#chat-channel").select_option(label="Party")
    page.locator("#chat-voice").click()
    expect(page.locator("#chat-voice-link a")).to_have_attribute("href", "https://discord.gg/code2")
    expect(page.locator("#chat-voice-link")).to_contain_text("works once")
