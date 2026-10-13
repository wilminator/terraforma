"""The account pages in a real browser: registering, confirming by link, resetting a password, the handle, the email address and
two-factor login, the way a player does them (the calls behind them are tests/test_accounts.py and tests/test_twofa.py)."""

import base64
import hashlib
import re
from urllib.parse import parse_qs, urlencode, urlsplit

from playwright.sync_api import expect

from terraforma.accounts import twofa
from terraforma.accounts.service import set_handle
from terraforma.models import Account
from terraforma.settings import OidcClient

from .conftest import PASSWORD
from .test_smoke import log_in


def token_in(mail):
    return re.search(r"token=([A-Za-z0-9_.-]+)", mail.body).group(1)


def confirmed_and_logged_in(page, live_server, username="Mike"):
    live_server.make_account(username)
    page.goto("/account/")
    log_in(page, username)
    expect(page.locator("#who")).to_have_text(f"Logged in as {username}.")


def test_a_player_registers_confirms_by_the_link_and_logs_in(page, mailbox):
    page.goto("/account/")
    page.get_by_role("link", name="Create an account").click()
    page.get_by_label("Username").fill("Zed")
    page.get_by_label("Email address").fill("zed@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Password again").fill(PASSWORD)
    page.get_by_role("button", name="Create account").click()
    # (hashing the password takes a while when the whole suite runs at once: wait longer than the usual five seconds)
    expect(page.get_by_role("heading", name="Check your email")).to_be_visible(timeout=30_000)

    # The link opens a page that waits for the button: opening a link, which mail scanners do, changes nothing.
    page.goto(f"/confirm-email?token={token_in(mailbox.last_to('zed@example.com'))}")
    expect(page).to_have_url("/confirm-email")  # the token is taken out of the address bar
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("Your email address is confirmed, Zed.")).to_be_visible()

    page.get_by_role("link", name="Log in").click()
    log_in(page, "Zed")
    expect(page.locator("#who")).to_have_text("Logged in as Zed.")


def test_registering_with_two_different_passwords_asks_again_without_calling_the_server(page):
    page.goto("/account/#register")
    page.get_by_label("Username").fill("Zed")
    page.get_by_label("Email address").fill("zed@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Password again").fill(PASSWORD + "!")
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("alert").first).to_have_text("The two passwords are not the same.")


def test_a_taken_username_is_said_plainly(page, live_server, console):
    console.allow("status of 409")  # the browser logs the refusal itself
    live_server.make_account("Mike")
    page.goto("/account/#register")
    page.get_by_label("Username").fill("Mike")
    page.get_by_label("Email address").fill("other@example.com")
    page.get_by_label("Password", exact=True).fill(PASSWORD)
    page.get_by_label("Password again").fill(PASSWORD)
    page.get_by_role("button", name="Create account").click()
    expect(page.get_by_role("alert").first).not_to_be_empty()
    expect(page.get_by_role("heading", name="Check your email")).to_have_count(0)


def test_a_forgotten_password_is_reset_by_the_mailed_link(page, live_server, mailbox):
    live_server.make_account("Mike")
    page.goto("/account/")
    page.get_by_role("link", name="Forgot your password?").click()
    page.get_by_label("Email address").fill("mike@example.com")
    page.get_by_role("button", name="Send the link").click()
    expect(page.get_by_text("If mike@example.com has an account")).to_be_visible()

    page.goto(f"/reset-password?token={token_in(mailbox.last_to('mike@example.com'))}")
    page.get_by_label("New password").fill("a brand new password")
    page.get_by_label("Password again").fill("a brand new password")
    page.get_by_role("button", name="Save the new password").click()
    expect(page.get_by_text("Your password is changed")).to_be_visible()

    page.get_by_role("link", name="Log in").click()
    log_in(page, password="a brand new password")
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")


def test_a_reset_for_an_address_with_no_account_looks_the_same(page, mailbox):
    page.goto("/account/#reset")
    page.get_by_label("Email address").fill("nobody@example.com")
    page.get_by_role("button", name="Send the link").click()
    expect(page.get_by_text("If nobody@example.com has an account")).to_be_visible()
    assert mailbox.sent == []


def test_a_link_with_no_token_says_so(page):
    page.goto("/confirm-email")
    expect(page.get_by_role("heading", name="This link is incomplete")).to_be_visible()


def test_a_link_the_server_refuses_is_said_in_the_servers_words(page, console):
    console.allow("status of 400")
    page.goto("/confirm-email?token=not-a-real-token")
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_role("alert").first).not_to_be_empty()


def test_the_handle_is_set_and_may_not_be_the_username(page, live_server, console):
    console.allow("status of 422")
    confirmed_and_logged_in(page, live_server)
    expect(page.locator("#current-handle")).to_contain_text("no handle yet")
    page.get_by_label("New handle").fill("mike")
    page.get_by_role("button", name="Save handle").click()
    expect(page.locator("#handle-form .problem")).not_to_be_empty()

    page.get_by_label("New handle").fill("Sir Mike")
    page.get_by_role("button", name="Save handle").click()
    expect(page.locator("#current-handle")).to_have_text("Other players see you as Sir Mike.")
    page.reload()
    expect(page.locator("#current-handle")).to_have_text("Other players see you as Sir Mike.")


def test_an_email_change_asks_for_no_password_and_completes_from_the_new_address(page, live_server, mailbox):
    confirmed_and_logged_in(page, live_server)
    expect(page.locator("#email-code")).to_be_hidden()  # no code asked for while 2FA is off
    page.get_by_label("New email address").fill("mike.new@example.com")
    page.get_by_role("button", name="Send the link").click()
    expect(page.get_by_text("If the address can be used")).to_be_visible()
    assert "your email address is being changed" in mailbox.last_to("mike@example.com").subject

    page.goto(f"/change-email?token={token_in(mailbox.last_to('mike.new@example.com'))}")
    page.get_by_role("button", name="Change my email address").click()
    expect(page.get_by_text("Your email address is changed. Log in again.")).to_be_visible()
    # The server logged every session out, this one included.
    assert page.request.get("/api/session").json()["account"] is None


def test_two_factor_login_is_set_up_confirmed_by_link_used_and_turned_off(page, live_server, mailbox):
    confirmed_and_logged_in(page, live_server)
    page.get_by_role("button", name="Set up an authenticator app").click()
    secret = page.locator("#twofa-secret").inner_text().replace(" ", "")
    expect(page.locator("#twofa-uri")).to_have_attribute("href", re.compile(r"^otpauth://totp/"))

    page.locator("#twofa-enable-code").fill(twofa.code_at(secret, twofa.current_step()))
    page.get_by_role("button", name="Turn on").click()
    expect(page.get_by_text("A link to confirm this is on its way")).to_be_visible()

    # The link needs the account it was made for: this browser is still logged in as it, so the button is there at once.
    page.goto(f"/confirm-2fa?token={token_in(mailbox.last_to('mike@example.com'))}")
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("Two-factor login is on.")).to_be_visible()
    codes = page.locator("#recovery-codes li")
    expect(codes).to_have_count(10)
    recovery = codes.first.inner_text()

    # Logging in now asks for a second code, and a recovery code works.
    page.goto("/account/")
    page.get_by_role("button", name="Log out").click()
    page.get_by_label("Username").fill("Mike")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    page.get_by_label("Code from your authenticator app, or a recovery code").fill(recovery)
    page.get_by_role("button", name="Log in").click()
    expect(page.locator("#twofa-state")).to_have_text("Two-factor login is on. Recovery codes left: 9.")

    # With it on, an email change needs a code as well.
    expect(page.locator("#email-code")).to_be_visible()


def test_a_two_factor_link_opened_logged_out_asks_for_the_login_first(page, live_server, mailbox):
    confirmed_and_logged_in(page, live_server)
    page.get_by_role("button", name="Set up an authenticator app").click()
    secret = page.locator("#twofa-secret").inner_text().replace(" ", "")
    page.locator("#twofa-enable-code").fill(twofa.code_at(secret, twofa.current_step()))
    page.get_by_role("button", name="Turn on").click()
    expect(page.get_by_text("A link to confirm this is on its way")).to_be_visible()
    page.get_by_role("button", name="Log out").click()
    expect(page.get_by_role("heading", name="Log in")).to_be_visible()  # the logout has finished: the link below is opened logged out

    page.goto(f"/confirm-2fa?token={token_in(mailbox.last_to('mike@example.com'))}")
    expect(page.get_by_text("Log in to the account this link was made for to finish.")).to_be_visible()
    log_in(page)
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("Two-factor login is on.")).to_be_visible()


def test_the_account_pages_fit_a_phone_in_either_orientation(new_page, live_server):
    live_server.make_account()
    for viewport in ({"width": 390, "height": 800}, {"width": 800, "height": 390}):
        page = new_page(viewport)
        page.goto("/account/")
        log_in(page)
        expect(page.get_by_role("button", name="Save handle")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def test_a_security_key_is_added_confirmed_by_link_and_used_to_log_in(new_page, live_server, mailbox):
    # A security key is bound to the site's name: browsers refuse an IP address, so this test reaches the server as "localhost"
    # (and the server's public address says the same), with a virtual authenticator standing in for the key.
    port = live_server.url.rsplit(":", 1)[1]
    live_server.app.state.settings.public_url = f"http://localhost:{port}"
    page = new_page(url=f"http://localhost:{port}")
    cdp = page.context.new_cdp_session(page)
    cdp.send("WebAuthn.enable")
    cdp.send("WebAuthn.addVirtualAuthenticator", {"options": {
        "protocol": "ctap2", "transport": "internal", "hasResidentKey": True, "hasUserVerification": True,
        "isUserVerified": True, "automaticPresenceSimulation": True,
    }})
    confirmed_and_logged_in(page, live_server)

    page.get_by_label("A name for it").fill("Laptop")
    page.get_by_role("button", name="Add a security key").click()
    expect(page.get_by_text("A link to confirm this is on its way")).to_be_visible()
    page.goto(f"/confirm-2fa?token={token_in(mailbox.last_to('mike@example.com'))}")
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("Two-factor login is on.")).to_be_visible()
    expect(page.locator("#recovery-codes li")).to_have_count(10)

    page.goto("/account/")
    expect(page.locator("#twofa-keys")).to_contain_text("Laptop (not used yet)")
    page.get_by_role("button", name="Log out").click()
    page.get_by_label("Username").fill("Mike")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    page.get_by_role("button", name="Use my security key").click()
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")
    expect(page.locator("#login-note")).to_contain_text("only one second factor")
    expect(page.locator("#twofa-keys")).to_contain_text("Laptop (last used")


def test_a_remembered_browser_skips_the_second_factor_until_it_is_forgotten(page, live_server, mailbox):
    confirmed_and_logged_in(page, live_server)
    page.get_by_role("button", name="Set up an authenticator app").click()
    secret = page.locator("#twofa-secret").inner_text().replace(" ", "")
    page.locator("#twofa-enable-code").fill(twofa.code_at(secret, twofa.current_step()))
    page.get_by_role("button", name="Turn on").click()
    expect(page.get_by_text("A link to confirm this is on its way")).to_be_visible()
    page.goto(f"/confirm-2fa?token={token_in(mailbox.last_to('mike@example.com'))}")
    page.get_by_role("button", name="Confirm").click()
    expect(page.get_by_text("Two-factor login is on.")).to_be_visible()

    page.goto("/account/")
    page.get_by_role("button", name="Log out").click()
    page.get_by_label("Username").fill("Mike")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    # (the step after the one that turned it on: a code works once, and a neighbouring step is forgiven)
    page.locator("#login-code").fill(twofa.code_at(secret, twofa.current_step() + 1))
    page.get_by_label("Remember this browser for 30 days").check()
    page.get_by_role("button", name="Log in").click()
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")
    expect(page.locator("#twofa-browsers")).to_contain_text("(this browser, until")

    page.get_by_role("button", name="Log out").click()
    log_in(page)  # no second factor is asked for
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")

    page.get_by_role("button", name="Forget all browsers").click()
    expect(page.locator("#who")).to_have_text("Logged in as Mike.")
    expect(page.locator("#twofa-browsers")).to_contain_text("No browser is remembered.")
    page.get_by_role("button", name="Log out").click()
    page.get_by_label("Username").fill("Mike")
    page.get_by_label("Password").fill(PASSWORD)
    page.get_by_role("button", name="Log in").click()
    expect(page.locator("#login-code")).to_be_visible()


def test_a_site_that_asks_for_a_sign_in_sends_a_logged_out_player_through_the_login_and_back(page, live_server):
    """The sign-in provider (tests/test_oidc_provider.py has the rest): authorize, the login page, then back to the site with a code."""
    back = f"{live_server.url}/account/"  # a page of the game itself stands in for the site's address
    live_server.app.state.settings = live_server.app.state.settings.model_copy(
        update={"oidc_clients": [OidcClient(client_id="store", client_secret="s" * 32, redirect_uris=[back])]}
    )
    account_id = live_server.make_account("Mike").id

    async def choose_handle(db):
        await set_handle(db, await db.get(Account, account_id), "Sir Mike")

    live_server.in_db(choose_handle)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 50).digest()).rstrip(b"=").decode()
    asked = {"response_type": "code", "client_id": "store", "redirect_uri": back, "scope": "openid", "state": "xyz", "code_challenge": challenge, "code_challenge_method": "S256"}
    page.goto("/oidc/authorize?" + urlencode(asked))
    expect(page).to_have_url(re.compile(r"/account/\?next=oidc&request="))
    log_in(page, "Mike")
    # The login hands the player on to /oidc/resume, which sends them to the site's address with the code.
    page.wait_for_url(re.compile(r"[?&]code="), timeout=30_000)
    query = parse_qs(urlsplit(page.url).query)
    assert query["state"] == ["xyz"] and query["iss"] == [live_server.settings.public_url]
