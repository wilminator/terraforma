"""Signing in with Facebook (OAuth 2 and the Graph API) and Steam (OpenID 2.0): the two providers that are not OpenID Connect.

They share the routes, the session handling and the account rules with the OpenID Connect providers (see test_external_signin.py);
what differs is how the provider is talked to, so the providers are played by mock transports standing in for
graph.facebook.com and steamcommunity.com.
"""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from terraforma import wallclock
from terraforma.accounts import oidc
from terraforma.accounts.service import create_account
from terraforma.settings import ExternalProvider
from terraforma.testing import in_app_db

from .helpers import expect
from .test_external_signin import account_named, finish, identities_in_db, outcome

PASSWORD = "correct horse battery"
FACEBOOK_ID = "10158000000000001"
STEAM_ID = "76561198000000001"
EMAIL = "kim@example.com"
CALLBACK = "http://game.test/api/external/{}/callback"


def form_of(request: httpx.Request) -> dict:
    return {key: values[0] for key, values in parse_qs(request.content.decode()).items()}


# --- Facebook -----------------------------------------------------------------------------------------------------------------


class FakeFacebook:
    """graph.facebook.com: trades a code for an access token and says whose token it is."""

    def __init__(self):
        self.profile: dict = {"id": FACEBOOK_ID, "email": EMAIL}
        self.token_status = 200
        self.me_status = 200
        self.token_body: dict = {"access_token": "the-access-token", "token_type": "bearer", "expires_in": 5000}
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.url.host == "graph.facebook.com"
        if request.url.path == f"/{oidc.FACEBOOK_VERSION}/oauth/access_token":
            return httpx.Response(self.token_status, json=self.token_body if self.token_status == 200 else {"error": {"message": "bad code"}})
        if request.url.path == f"/{oidc.FACEBOOK_VERSION}/me":
            return httpx.Response(self.me_status, json=self.profile if self.me_status == 200 else {"error": {"message": "bad token"}})
        return httpx.Response(404)


@pytest.fixture
def facebook():
    return FakeFacebook()


@pytest.fixture
def fb_client(app_client, facebook):
    app_client.app.state.settings.external_providers = [
        ExternalProvider(name="facebook", label="Facebook", kind="facebook", client_id="app-1", client_secret="app-secret-1")
    ]
    app_client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(facebook.handle))
    return app_client


def facebook_start(client) -> dict:
    answer = client.get("/api/external/facebook/start", follow_redirects=False)
    assert answer.status_code == 303, answer.text
    address = urlsplit(answer.headers["location"])
    assert f"{address.scheme}://{address.netloc}{address.path}" == oidc.FACEBOOK_AUTHORIZATION
    return {key: values[0] for key, values in parse_qs(address.query).items()}


def facebook_back(client, sent, **query):
    return client.get("/api/external/facebook/callback", params={"code": "the-code", "state": sent["state"], **query}, follow_redirects=False)


def facebook_sign_in(client):
    return facebook_back(client, facebook_start(client))


def test_facebook_needs_an_app_id_and_secret_and_has_no_addresses_to_set():
    ExternalProvider(name="facebook", label="Facebook", kind="facebook", client_id="a", client_secret="b")
    base = {"name": "facebook", "label": "Facebook", "kind": "facebook", "client_id": "a", "client_secret": "b"}
    for bad in (
        {"client_secret": ""},
        {"client_id": ""},
        {"issuer": "https://www.facebook.com"},
        {"authorization_endpoint": "https://x.example/a", "token_endpoint": "https://x.example/t"},
        {"scopes": ["openid", "email", "extra"]},
    ):
        with pytest.raises(ValidationError):
            ExternalProvider(**{**base, **bad})


def test_facebook_is_sent_to_the_login_dialog_with_a_state_and_only_the_email_permission(fb_client):
    sent = facebook_start(fb_client)
    assert sent["response_type"] == "code" and sent["client_id"] == "app-1"
    assert sent["redirect_uri"] == CALLBACK.format("facebook")
    assert sent["scope"] == "email"
    assert len(sent["state"]) >= 32
    assert "client_secret" not in sent
    assert fb_client.get("/api/external/providers").json() == {"providers": [{"name": "facebook", "label": "Facebook"}]}


def test_facebook_first_sign_in_trades_the_code_asks_the_graph_api_and_waits_for_a_username(fb_client, facebook):
    assert outcome(facebook_sign_in(fb_client)) == {"external": "finish"}
    token_call, me_call = facebook.requests
    form = form_of(token_call)
    assert form["code"] == "the-code" and form["client_id"] == "app-1" and form["client_secret"] == "app-secret-1"
    assert form["redirect_uri"] == CALLBACK.format("facebook")
    assert me_call.headers["authorization"] == "Bearer the-access-token"
    proof = hmac.new(b"app-secret-1", b"the-access-token", hashlib.sha256).hexdigest()
    assert parse_qs(me_call.url.query.decode()) == {"fields": ["id,email"], "appsecret_proof": [proof]}
    assert fb_client.get("/api/external/pending").json() == {"pending": {"provider": "Facebook", "email": EMAIL}}


def test_facebook_finishing_makes_a_confirmed_account_and_a_returning_player_logs_in(fb_client):
    assert outcome(facebook_sign_in(fb_client))["external"] == "finish"
    expect(finish(fb_client), 200)
    assert identities_in_db(fb_client) == [("facebook", FACEBOOK_ID, EMAIL)]
    assert account_named(fb_client, "Kim").email_confirmed_at is not None
    fb_client.cookies.clear()
    assert outcome(facebook_sign_in(fb_client)) == {"external": "done"}
    assert fb_client.get("/api/me").json()["username"] == "Kim"


@pytest.mark.parametrize("profile", [{"id": FACEBOOK_ID}, {"id": FACEBOOK_ID, "email": ""}, {"id": FACEBOOK_ID, "email": "not an address"}])
def test_facebook_without_an_email_makes_no_account(fb_client, facebook, profile):
    facebook.profile = profile
    assert outcome(facebook_sign_in(fb_client)) == {"external": "error", "reason": "email_unverified"}
    assert fb_client.get("/api/external/pending").json() == {"pending": None}


def test_facebook_with_an_email_that_has_an_account_creates_and_links_nothing(fb_client):
    in_app_db(fb_client, lambda db: create_account(db, "Mike", PASSWORD, email=EMAIL, confirmed=True))
    assert outcome(facebook_sign_in(fb_client)) == {"external": "error", "reason": "email_in_use"}
    assert identities_in_db(fb_client) == []


@pytest.mark.parametrize(
    "change",
    [
        {"token_status": 400},
        {"token_body": {"token_type": "bearer"}},
        {"token_body": {"access_token": ""}},
        {"me_status": 401},
        {"profile": {"email": EMAIL}},
        {"profile": {"id": 12345, "email": EMAIL}},
        {"profile": ["not", "an", "object"]},
    ],
)
def test_facebook_failing_anywhere_is_an_error_page_and_makes_nothing(fb_client, facebook, change):
    for key, value in change.items():
        setattr(facebook, key, value)
    assert outcome(facebook_sign_in(fb_client)) == {"external": "error", "reason": "failed"}
    assert identities_in_db(fb_client) == [] and fb_client.get("/api/external/pending").json() == {"pending": None}


def test_facebook_refused_by_the_player_or_with_the_wrong_state(fb_client, facebook):
    sent = facebook_start(fb_client)
    answer = fb_client.get("/api/external/facebook/callback", params={"error": "access_denied", "state": sent["state"]}, follow_redirects=False)
    assert outcome(answer) == {"external": "error", "reason": "denied"}
    assert facebook.requests == []
    facebook_start(fb_client)
    assert outcome(facebook_back(fb_client, {"state": "x" * 43})) == {"external": "error", "reason": "expired"}
    assert facebook.requests == [], "nothing is asked of Facebook for a sign-in that was not started here"


def test_facebook_can_be_linked_by_a_logged_in_player(fb_client):
    in_app_db(fb_client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": fb_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    address = expect(fb_client.post("/api/external/facebook/link", json={}, headers=headers), 200).json()["url"]
    sent = {key: values[0] for key, values in parse_qs(urlsplit(address).query).items()}
    assert outcome(facebook_back(fb_client, sent)) == {"external": "linked"}
    assert identities_in_db(fb_client) == [("facebook", FACEBOOK_ID, EMAIL)]
    assert account_named(fb_client, "Mike").email == "mike@example.com", "linking never changes the account's email"


# --- Steam --------------------------------------------------------------------------------------------------------------------


class FakeSteam:
    """steamcommunity.com: confirms or denies that it signed an answer (check_authentication)."""

    def __init__(self):
        self.valid = True
        self.status = 200
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert (request.url.host, request.url.path) == ("steamcommunity.com", "/openid/login")
        if self.status != 200:
            return httpx.Response(self.status)
        return httpx.Response(200, text=f"ns:{oidc.STEAM_NAMESPACE}\nis_valid:{'true' if self.valid else 'false'}\n")


@pytest.fixture
def steam():
    return FakeSteam()


@pytest.fixture
def steam_client(app_client, steam):
    app_client.app.state.settings.external_providers = [ExternalProvider(name="steam", label="Steam", kind="steam")]
    app_client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(steam.handle))
    return app_client


def steam_start(client) -> dict:
    answer = client.get("/api/external/steam/start", follow_redirects=False)
    assert answer.status_code == 303, answer.text
    address = urlsplit(answer.headers["location"])
    assert f"{address.scheme}://{address.netloc}{address.path}" == oidc.STEAM_LOGIN
    return {key: values[0] for key, values in parse_qs(address.query).items()}


def steam_answer(sent: dict, **changes) -> dict:
    """What Steam adds to the redirect back, for the sign-in that sent the browser away with $sent."""
    stamp = wallclock.now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    claimed = f"https://steamcommunity.com/openid/id/{STEAM_ID}"
    answer = {
        "openid.ns": oidc.STEAM_NAMESPACE,
        "openid.mode": "id_res",
        "openid.op_endpoint": oidc.STEAM_LOGIN,
        "openid.claimed_id": claimed,
        "openid.identity": claimed,
        "openid.return_to": sent["openid.return_to"],
        "openid.response_nonce": stamp + "AbCdEf",
        "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.sig": "c2ln",
        **changes,
    }
    return {key: value for key, value in answer.items() if value is not None}


def steam_back(client, sent, answer=None, state=None):
    state = state or parse_qs(urlsplit(sent["openid.return_to"]).query)["state"][0]
    params = {"state": state, **(steam_answer(sent) if answer is None else answer)}
    return client.get("/api/external/steam/callback", params=params, follow_redirects=False)


def test_steam_needs_nothing_but_a_name_and_label_and_takes_no_keys():
    ExternalProvider(name="steam", label="Steam", kind="steam")
    for bad in ({"client_id": "x"}, {"client_secret": "x"}, {"issuer": "https://steamcommunity.com"}, {"scopes": ["openid", "email", "x"]}):
        with pytest.raises(ValidationError):
            ExternalProvider(name="steam", label="Steam", kind="steam", **bad)
    with pytest.raises(ValidationError):
        ExternalProvider(name="x", label="X", kind="myspace")


def test_steam_is_sent_to_its_openid_login_with_a_return_address_that_carries_the_state(steam_client):
    sent = steam_start(steam_client)
    assert sent["openid.mode"] == "checkid_setup" and sent["openid.ns"] == oidc.STEAM_NAMESPACE
    assert sent["openid.realm"] == "http://game.test"
    assert sent["openid.identity"] == sent["openid.claimed_id"] == oidc.STEAM_IDENTIFIER_SELECT
    return_to = urlsplit(sent["openid.return_to"])
    assert f"{return_to.scheme}://{return_to.netloc}{return_to.path}" == CALLBACK.format("steam")
    assert len(parse_qs(return_to.query)["state"][0]) >= 32
    assert steam_client.get("/api/external/providers").json() == {"providers": [{"name": "steam", "label": "Steam"}]}


def link_steam(client):
    """A logged-in Mike links Steam. Returns nothing; Mike is logged out afterwards."""
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    address = expect(client.post("/api/external/steam/link", json={}, headers=headers), 200).json()["url"]
    sent = {key: values[0] for key, values in parse_qs(urlsplit(address).query).items()}
    assert outcome(steam_back(client, sent)) == {"external": "linked"}
    client.cookies.clear()


def test_steam_links_to_an_account_and_then_signs_in_to_it(steam_client, steam):
    link_steam(steam_client)
    assert identities_in_db(steam_client) == [("steam", STEAM_ID, None)], "Steam gives no email address"
    assert account_named(steam_client, "Mike").email == "mike@example.com"
    assert outcome(steam_back(steam_client, steam_start(steam_client))) == {"external": "done"}
    assert steam_client.get("/api/me").json()["username"] == "Mike"


def test_steam_asks_steam_to_confirm_its_signature_with_every_value_it_sent(steam_client, steam):
    link_steam(steam_client)
    steam.requests.clear()
    sent = steam_start(steam_client)
    steam_back(steam_client, sent)
    (call,) = steam.requests
    assert call.method == "POST"
    assert form_of(call) == {**steam_answer(sent, **{"openid.response_nonce": form_of(call)["openid.response_nonce"]}), "openid.mode": "check_authentication"}


def test_a_first_steam_sign_in_makes_no_account_because_steam_gives_no_email(steam_client):
    assert outcome(steam_back(steam_client, steam_start(steam_client))) == {"external": "error", "reason": "no_account"}
    assert identities_in_db(steam_client) == []
    assert steam_client.get("/api/external/pending").json() == {"pending": None}
    assert steam_client.get("/api/session").json()["account"] is None


def test_a_steam_id_linked_to_someone_else_is_refused_when_linking(steam_client):
    link_steam(steam_client)
    in_app_db(steam_client, lambda db: create_account(db, "Kim", PASSWORD, email=EMAIL, confirmed=True))
    headers = {"X-CSRF-Token": steam_client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).json()["csrf_token"]}
    address = expect(steam_client.post("/api/external/steam/link", json={}, headers=headers), 200).json()["url"]
    sent = {key: values[0] for key, values in parse_qs(urlsplit(address).query).items()}
    assert outcome(steam_back(steam_client, sent)) == {"external": "error", "reason": "taken"}


def test_the_player_cancelling_at_steam_is_a_denial_and_steam_is_not_asked(steam_client, steam):
    sent = steam_start(steam_client)
    cancel = {"openid.ns": oidc.STEAM_NAMESPACE, "openid.mode": "cancel"}
    assert outcome(steam_back(steam_client, sent, cancel)) == {"external": "error", "reason": "denied"}
    assert steam.requests == []


@pytest.mark.parametrize(
    "changes",
    [
        {"openid.mode": "setup_needed"},
        {"openid.ns": "http://specs.openid.net/auth/1.1"},
        {"openid.op_endpoint": "https://evil.example/openid/login"},
        {"openid.return_to": CALLBACK.format("steam") + "?state=" + "a" * 43},
        {"openid.return_to": "https://evil.example/cb"},
        {"openid.claimed_id": "https://evil.example/openid/id/" + STEAM_ID, "openid.identity": "https://evil.example/openid/id/" + STEAM_ID},
        {"openid.claimed_id": "https://steamcommunity.com/openid/id/notanumber"},
        {"openid.identity": "https://steamcommunity.com/openid/id/76561198000000002"},
        {"openid.claimed_id": None},
        {"openid.signed": "signed,op_endpoint,claimed_id,identity,response_nonce"},
        {"openid.signed": "return_to"},
        {"openid.signed": None},
        {"openid.response_nonce": (datetime.now(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ") + "old"},
        {"openid.response_nonce": "not a time"},
        {"openid.response_nonce": None},
        {"openid.junk": "x" * 2000},
    ],
)
def test_a_steam_answer_that_does_not_check_out_signs_nobody_in(steam_client, steam, changes):
    link_steam(steam_client)
    sent = steam_start(steam_client)
    steam.requests.clear()
    answer = steam_answer(sent, **changes)
    assert outcome(steam_back(steam_client, sent, answer)) == {"external": "error", "reason": "failed"}
    assert steam_client.get("/api/session").json()["account"] is None
    assert steam.requests == [], "Steam is asked only about an answer that already looks right"


def test_too_many_values_are_not_steams_answer(steam_client, steam):
    link_steam(steam_client)
    sent = steam_start(steam_client)
    answer = {**steam_answer(sent), **{f"openid.extra{n}": "x" for n in range(oidc.STEAM_MAX_PARAMS)}}
    assert outcome(steam_back(steam_client, sent, answer))["reason"] == "failed"


def test_steam_saying_it_did_not_sign_the_answer_signs_nobody_in(steam_client, steam):
    link_steam(steam_client)
    steam.valid = False
    assert outcome(steam_back(steam_client, steam_start(steam_client))) == {"external": "error", "reason": "failed"}
    assert steam_client.get("/api/session").json()["account"] is None


def test_steam_being_unreachable_is_an_error_page_not_a_crash(steam_client, steam):
    link_steam(steam_client)
    steam.status = 500
    assert outcome(steam_back(steam_client, steam_start(steam_client))) == {"external": "error", "reason": "failed"}


def test_a_steam_answer_for_a_sign_in_not_started_here_is_expired(steam_client, steam):
    link_steam(steam_client)
    sent = steam_start(steam_client)
    steam.requests.clear()
    assert outcome(steam_back(steam_client, sent, state="x" * 43)) == {"external": "error", "reason": "expired"}
    assert steam.requests == []


def test_a_steam_sign_in_works_once(steam_client):
    link_steam(steam_client)
    sent = steam_start(steam_client)
    assert outcome(steam_back(steam_client, sent))["external"] == "done"
    steam_client.cookies.clear()
    assert outcome(steam_back(steam_client, sent))["reason"] == "expired", "the same answer replayed finds no sign-in under way"


def test_the_account_page_words_the_no_account_reason():
    from pathlib import Path

    client_js = Path(oidc.__file__).parents[1] / "client" / "static" / "lib" / "js"
    assert '"external.error_no_account"' in (client_js / "strings.js").read_text()
    assert '"no_account"' in (client_js / "external.js").read_text()
