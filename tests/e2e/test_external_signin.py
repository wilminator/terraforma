"""Signing in with an external provider in a real browser. The provider is played by the test: the browser's trip to its sign-in
address is answered by Playwright (a redirect straight back with a code), and the server's call to its token address by a mock
transport (the calls behind all this are tests/test_external_signin.py)."""

import base64
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
from playwright.sync_api import expect

from terraforma.settings import ExternalProvider

ISSUER = "https://idp.test"
EMAIL = "kim@example.com"


def b64(data):
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


def play_provider(page, live_server, email=EMAIL):
    """Makes the server's provider "Fake" and answers the browser's trip to it."""
    state = {}

    def token(request):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": "client-1", "sub": "subject-1", "email": email, "email_verified": True, "exp": now + 600, "nonce": state["nonce"]}
        return httpx.Response(200, json={"id_token": f"{b64({'alg': 'none'})}.{b64(claims)}."})

    live_server.app.state.settings.external_providers = [
        ExternalProvider(name="fake", label="Fake", issuer=ISSUER, client_id="client-1", client_secret="client-secret-1", authorization_endpoint=f"{ISSUER}/authorize", token_endpoint=f"{ISSUER}/token")
    ]
    live_server.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(token))

    def start(route):
        # The server answers the start with a redirect to the provider; the browser would follow it to the provider's own page,
        # so the test reads where it points and sends the browser straight back with a code instead (the session cookie the
        # server set rides along).
        answer = route.fetch(max_redirects=0)
        sent = {key: values[0] for key, values in parse_qs(urlsplit(answer.headers["location"]).query).items()}
        state["nonce"] = sent["nonce"]
        back = f"{live_server.url}/api/external/fake/callback?code=the-code&state={sent['state']}"
        route.fulfill(response=answer, status=302, headers={**answer.headers, "location": back})

    def authorize(route):
        # Linking sends the browser to the provider's address itself (the server answered it as data, not as a redirect).
        sent = {key: values[0] for key, values in parse_qs(urlsplit(route.request.url).query).items()}
        state["nonce"] = sent["nonce"]
        route.fulfill(status=302, headers={"Location": f"{live_server.url}/api/external/fake/callback?code=the-code&state={sent['state']}"})

    page.route(re.compile(r"/api/external/fake/start"), start)
    page.route(re.compile(r"^https://idp\.test/authorize"), authorize)


def test_a_player_signs_up_with_a_provider_chooses_a_name_and_comes_back_without_one(page, live_server):
    play_provider(page, live_server)
    page.goto("/account/")
    page.get_by_role("link", name="Continue with Fake").click()
    expect(page.get_by_role("heading", name="Choose your name")).to_be_visible()
    expect(page.get_by_text(EMAIL)).to_be_visible()
    page.get_by_label("Username").fill("Kim")
    page.get_by_label("New handle").fill("Sparrow")
    page.get_by_role("button", name="Create my account").click()
    expect(page.locator("#who")).to_have_text("Logged in as Kim.")
    expect(page.locator("#sign-in-methods")).to_contain_text("Fake")
    expect(page.locator("#sign-in-methods")).to_contain_text("only way to log in")

    page.get_by_role("button", name="Log out").click()
    page.get_by_role("link", name="Continue with Fake").click()
    expect(page.locator("#who")).to_have_text("Logged in as Kim.")


def test_an_email_that_has_an_account_is_said_plainly_and_nothing_is_made(page, live_server):
    live_server.make_account("Kim")  # kim@example.com
    play_provider(page, live_server)
    page.goto("/account/")
    page.get_by_role("link", name="Continue with Fake").click()
    expect(page.locator("#external-problem")).to_contain_text("already uses that email address")
    expect(page.get_by_role("button", name="Log in")).to_be_visible()


def test_a_logged_in_player_links_a_provider_and_then_unlinks_it(page, live_server):
    live_server.make_account("Kim")
    play_provider(page, live_server, email="someone-else@example.com")
    page.goto("/account/")
    page.get_by_label("Username").fill("Kim")
    page.get_by_label("Password").fill("correct horse battery")
    page.get_by_role("button", name="Log in").click()
    page.get_by_role("button", name="Link Fake").click()
    expect(page.locator("#external-note")).to_have_text("The provider is linked. You can sign in with it from now on.")
    expect(page.locator("#sign-in-methods")).to_contain_text("Fake")
    page.get_by_role("button", name="Unlink").click()
    expect(page.get_by_role("button", name="Link Fake")).to_be_visible()
