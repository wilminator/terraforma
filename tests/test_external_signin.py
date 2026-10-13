"""Signing in with an external provider (OpenID Connect): first sign-in, returning, linking, unlinking, two-factor, and every way it can go wrong.

The provider is played by FakeProvider, an httpx mock transport standing in for the token address; the browser's trip to the
provider is skipped, but the address the server would send it to is read and checked.
"""

import base64
import json
import re
from urllib.parse import parse_qs, urlsplit

import httpx2 as httpx
import pytest
from pydantic import ValidationError

from terraforma import wallclock
from terraforma.accounts import ratelimit, twofa
from terraforma.accounts.oidc import challenge
from terraforma.accounts.service import create_account
from terraforma.settings import ExternalProvider, Settings
from terraforma.testing import in_app_db

from .helpers import expect

PASSWORD = "correct horse battery"
SUBJECT = "google-subject-1"
EMAIL = "kim@example.com"
ISSUER = "https://idp.test"


def b64(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


class FakeProvider:
    """Answers the server's calls to the provider, and builds the ID token for whoever is "signing in"."""

    def __init__(self):
        self.claims: dict = {}
        self.challenge: str | None = None
        self.token_status = 200
        self.requests: list[httpx.Request] = []
        self.forms: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/token":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.forms.append(form)
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_grant"})
            assert challenge(form["code_verifier"]) == self.challenge, "the verifier must match the challenge the browser was sent with"
            return httpx.Response(200, json={"id_token": f"{b64({'alg': 'none'})}.{b64(self.claims)}.", "access_token": "unused"})
        return httpx.Response(404)


@pytest.fixture
def idp():
    return FakeProvider()


def provider(**changes) -> ExternalProvider:
    return ExternalProvider(
        **{
            "name": "fake",
            "label": "Fake",
            "issuer": ISSUER,
            "client_id": "client-1",
            "client_secret": "client-secret-1",
            "authorization_endpoint": f"{ISSUER}/authorize",
            "token_endpoint": f"{ISSUER}/token",
            **changes,
        }
    )


@pytest.fixture
def client(app_client, idp):
    app_client.app.state.settings.external_providers = [provider()]
    app_client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
    return app_client


def claims(**changes) -> dict:
    now = int(wallclock.timestamp())
    return {"iss": ISSUER, "aud": "client-1", "sub": SUBJECT, "email": EMAIL, "email_verified": True, "exp": now + 600, "iat": now, **changes}


def go_to_provider(client, idp, path="/api/external/fake/start", method="get"):
    """Starts a sign-in: returns the parameters the browser would be sent to the provider with."""
    answer = client.get(path, follow_redirects=False) if method == "get" else client.post(path, json={}, headers=method)
    return browse(answer, idp)


def browse(answer, idp) -> dict:
    if answer.headers.get("content-type", "").startswith("application/json"):
        address = answer.json()["url"]
    else:
        assert answer.status_code == 303, answer.text
        address = answer.headers["location"]
    parts = urlsplit(address)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{ISSUER}/authorize"
    sent = {key: values[0] for key, values in parse_qs(parts.query).items()}
    idp.challenge = sent["code_challenge"]
    return sent


def come_back(client, sent, idp, *, state=None, code="the-code", **token_claims):
    """The provider sends the browser back with a code; $token_claims change what the ID token says."""
    idp.claims = claims(nonce=sent["nonce"], **token_claims)
    return client.get("/api/external/fake/callback", params={"code": code, "state": state or sent["state"], "iss": ISSUER, "authuser": "0"}, follow_redirects=False)


def sign_in(client, idp, **token_claims):
    return come_back(client, go_to_provider(client, idp), idp, **token_claims)


def outcome(answer) -> dict:
    """Where the callback sent the browser: {"external": ..., "reason": ...}."""
    assert answer.status_code == 303, answer.text
    parts = urlsplit(answer.headers["location"])
    assert parts.path == "/account/"
    return {key: values[0] for key, values in parse_qs(parts.query).items()}


def finish(client, **changes):
    return client.post("/api/external/finish", json={"username": "Kim", "handle": "Sparrow", **changes})


def new_player(client, idp, **changes):
    """Signs a player in through the provider for the first time and finishes the account. Returns the CSRF headers."""
    assert outcome(sign_in(client, idp))["external"] == "finish"
    answer = expect(finish(client, **changes), 200)
    return {"X-CSRF-Token": answer.json()["csrf_token"]}


def account_named(client, username):
    from sqlalchemy import select

    from terraforma.models import Account

    async def get(db):
        return await db.scalar(select(Account).where(Account.username == username))

    return in_app_db(client, get)


def identities_in_db(client):
    from sqlalchemy import select

    from terraforma.models import ExternalIdentity

    async def get(db):
        return [(each.provider, each.subject, each.email) for each in await db.scalars(select(ExternalIdentity))]

    return in_app_db(client, get)


def code_now(secret: str) -> str:
    return twofa.code_at(secret, twofa.current_step())


# --- settings --------------------------------------------------------------------------------------------------------------

def test_a_provider_needs_a_clean_name_and_https_addresses_and_openid_with_email():
    provider()
    for bad in ({"name": "Has Space"}, {"name": ""}, {"issuer": "http://idp.example.com"}, {"scopes": ["openid"]}, {"scopes": ["email"]}, {"client_secret": ""}):
        with pytest.raises(ValidationError):
            provider(**bad)
    provider(issuer="http://localhost:9000", authorization_endpoint="http://localhost:9000/a", token_endpoint="http://localhost:9000/t")


def test_the_two_endpoints_are_given_together_or_not_at_all():
    with pytest.raises(ValidationError):
        provider(token_endpoint=None)
    ExternalProvider(name="google", label="Google", issuer="https://accounts.google.com", client_id="a", client_secret="b")


def test_two_providers_may_not_share_a_name():
    with pytest.raises(ValidationError):
        Settings(session_secret="x" * 32, external_providers=[provider(), provider(label="Again")])


def test_no_providers_by_default(app_client):
    assert app_client.get("/api/external/providers").json() == {"providers": []}


# --- going to the provider -------------------------------------------------------------------------------------------------

def test_the_buttons_list_names_and_labels_only(client):
    answer = client.get("/api/external/providers").json()
    assert answer == {"providers": [{"name": "fake", "label": "Fake"}]}
    assert "client-secret-1" not in json.dumps(answer)


def test_starting_sends_the_browser_to_the_provider_with_state_nonce_and_a_pkce_challenge(client, idp):
    sent = go_to_provider(client, idp)
    assert sent["response_type"] == "code"
    assert sent["client_id"] == "client-1"
    assert sent["redirect_uri"] == "http://game.test/api/external/fake/callback"
    assert sent["scope"] == "openid email"
    assert sent["code_challenge_method"] == "S256"
    assert len(sent["state"]) >= 32 and len(sent["nonce"]) >= 32
    assert "client_secret" not in sent


def test_each_start_makes_new_secrets(client, idp):
    first, second = go_to_provider(client, idp), go_to_provider(client, idp)
    assert first["state"] != second["state"] and first["nonce"] != second["nonce"]


def test_an_unknown_provider_is_not_found(client):
    assert client.get("/api/external/nobody/start", follow_redirects=False).status_code == 404
    assert client.get("/api/external/nobody/callback", params={"code": "x", "state": "y"}, follow_redirects=False).status_code == 404


def test_starting_remembers_only_a_known_next_page(client, idp):
    assert client.get("/api/external/fake/start", params={"next": "https://evil.example/"}, follow_redirects=False).status_code == 422
    sent = browse(client.get("/api/external/fake/start", params={"next": "play"}, follow_redirects=False), idp)
    assert outcome(come_back(client, sent, idp))["next"] == "play"


def test_starting_is_rate_limited_by_address(client):
    for _ in range(ratelimit.EXTERNAL_START_BY_ADDRESS.attempts):
        assert client.get("/api/external/fake/start", follow_redirects=False).status_code == 303
    assert client.get("/api/external/fake/start", follow_redirects=False).status_code == 429


def test_the_provider_settings_are_found_by_discovery_when_not_given(client, idp):
    seen = []

    def handle(request):
        seen.append(request.url.path)
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize", "token_endpoint": f"{ISSUER}/token"})
        return idp.handle(request)

    client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client.app.state.settings.external_providers = [provider(authorization_endpoint=None, token_endpoint=None)]
    sent = go_to_provider(client, idp)
    assert outcome(come_back(client, sent, idp))["external"] == "finish"
    assert "/.well-known/openid-configuration" in seen


def test_a_provider_that_cannot_be_reached_is_an_error_page_not_a_crash(client):
    client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    client.app.state.settings.external_providers = [provider(authorization_endpoint=None, token_endpoint=None)]
    assert outcome(client.get("/api/external/fake/start", follow_redirects=False)) == {"external": "error", "reason": "failed"}


# --- a first sign-in -------------------------------------------------------------------------------------------------------

def test_a_first_sign_in_waits_for_a_username_and_handle_and_makes_no_account_yet(client, idp):
    assert outcome(sign_in(client, idp)) == {"external": "finish"}
    assert client.get("/api/session").json()["account"] is None, "not logged in yet"
    assert client.get("/api/external/pending").json() == {"pending": {"provider": "Fake", "email": EMAIL}}
    assert account_named(client, "Kim") is None
    assert identities_in_db(client) == []


def test_the_code_is_traded_with_the_secret_and_the_verifier(client, idp):
    sign_in(client, idp)
    form = idp.forms[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "the-code"
    assert form["client_id"] == "client-1" and form["client_secret"] == "client-secret-1"
    assert form["redirect_uri"] == "http://game.test/api/external/fake/callback"


def test_finishing_makes_a_confirmed_account_without_a_password_and_logs_in(client, idp):
    headers = new_player(client, idp)
    account = account_named(client, "Kim")
    assert (account.email, account.handle, account.password_hash) == (EMAIL, "Sparrow", None)
    assert account.email_confirmed_at is not None, "the provider vouched for the address"
    assert identities_in_db(client) == [("fake", SUBJECT, EMAIL)]
    assert client.get("/api/me").json() == {"username": "Kim", "handle": "Sparrow"}
    assert client.get("/api/external/identities").json() == {
        "identities": [{"provider": "fake", "label": "Fake", "email": EMAIL}],
        "has_password": False,
    }
    assert headers["X-CSRF-Token"]
    assert client.get("/api/external/pending").json() == {"pending": None}, "the finish step works once"


def test_a_password_chosen_at_the_finish_step_works_for_the_ordinary_login(client, idp):
    assert outcome(sign_in(client, idp))["external"] == "finish"
    expect(finish(client, password=PASSWORD), 200)
    client.post("/api/logout", headers={"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]})
    assert client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).status_code == 200


def test_an_account_with_no_password_cannot_be_logged_into_with_one(client, idp):
    new_player(client, idp)
    client.cookies.clear()
    assert client.post("/api/login", json={"username": "Kim", "password": ""}).status_code in {401, 422}
    assert client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).status_code == 401


def test_the_finish_step_checks_the_username_and_the_handle_and_can_be_retried(client, idp):
    assert outcome(sign_in(client, idp))["external"] == "finish"
    assert finish(client, username="no").status_code == 422, "too short"
    assert finish(client, handle="kim").status_code == 422, "a handle can't match the email's name part"
    assert finish(client, handle="Kim!!").status_code == 422
    assert finish(client, username="Sparrow", handle="Sparrow").status_code == 422, "a handle can't match the username"
    assert finish(client, password="short").status_code == 422
    expect(finish(client), 200)


def test_a_taken_username_or_handle_is_a_conflict_and_the_sign_in_waits(client, idp):
    in_app_db(client, lambda db: create_account(db, "Kim", PASSWORD, email="other@example.com", confirmed=True))
    assert outcome(sign_in(client, idp))["external"] == "finish"
    assert finish(client).status_code == 409
    assert finish(client, username="Kimmy").status_code == 200


def test_the_finish_step_needs_a_sign_in_first(client):
    assert finish(client).status_code == 409


def test_the_finish_step_is_rate_limited(client):
    for _ in range(ratelimit.EXTERNAL_FINISH_BY_ADDRESS.attempts):
        assert finish(client).status_code == 409
    assert finish(client).status_code == 429


# --- a returning sign-in ---------------------------------------------------------------------------------------------------

def test_a_returning_player_is_logged_in_at_once(client, idp):
    new_player(client, idp)
    client.cookies.clear()
    assert outcome(sign_in(client, idp)) == {"external": "done"}
    assert client.get("/api/me").json() == {"username": "Kim", "handle": "Sparrow"}


def test_a_returning_player_is_known_by_the_subject_not_the_email(client, idp):
    new_player(client, idp)
    client.cookies.clear()
    assert outcome(sign_in(client, idp, email="renamed@example.com"))["external"] == "done"
    assert client.get("/api/me").json()["username"] == "Kim"


def test_the_next_page_survives_the_trip(client, idp):
    new_player(client, idp)
    client.cookies.clear()
    sent = browse(client.get("/api/external/fake/start", params={"next": "profile"}, follow_redirects=False), idp)
    assert outcome(come_back(client, sent, idp)) == {"external": "done", "next": "profile"}


# --- an email that already has an account ----------------------------------------------------------------------------------

def test_an_email_with_an_account_creates_and_links_nothing_and_does_not_say_how_that_account_signs_in(client, idp):
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email=EMAIL, confirmed=True))
    answer = sign_in(client, idp)
    assert outcome(answer) == {"external": "error", "reason": "email_in_use"}
    assert identities_in_db(client) == []
    assert client.get("/api/external/pending").json() == {"pending": None}
    assert client.get("/api/session").json()["account"] is None, "never logged in by email alone"
    assert PASSWORD not in answer.headers["location"] and "Mike" not in answer.headers["location"]


def test_the_email_match_ignores_case_and_spaces(client, idp):
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="Kim@Example.com", confirmed=True))
    assert outcome(sign_in(client, idp, email=" kim@example.com "))["reason"] == "email_in_use"


@pytest.mark.parametrize("changes", [{"email_verified": False}, {"email_verified": "false"}, {"email_verified": None}, {"email": ""}, {"email": "not an address"}])
def test_an_email_the_provider_does_not_vouch_for_is_not_used(client, idp, changes):
    assert outcome(sign_in(client, idp, **changes)) == {"external": "error", "reason": "email_unverified"}
    assert client.get("/api/external/pending").json() == {"pending": None}


def test_a_provider_that_sends_the_verified_flag_as_the_text_true_is_accepted(client, idp):
    assert outcome(sign_in(client, idp, email_verified="true"))["external"] == "finish"


# --- every way the sign-in itself can fail ---------------------------------------------------------------------------------

def test_a_wrong_state_is_refused_and_the_sign_in_is_over(client, idp):
    sent = go_to_provider(client, idp)
    assert outcome(come_back(client, sent, idp, state="x" * 43)) == {"external": "error", "reason": "expired"}
    assert outcome(come_back(client, sent, idp)) == {"external": "error", "reason": "expired"}, "the flow works once"
    assert idp.forms == [], "the code was never traded"


def test_a_callback_with_no_sign_in_under_way_is_refused(client, idp):
    answer = client.get("/api/external/fake/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert outcome(answer) == {"external": "error", "reason": "expired"}


def test_a_callback_replayed_after_success_is_refused(client, idp):
    sent = go_to_provider(client, idp)
    assert outcome(come_back(client, sent, idp))["external"] == "finish"
    assert outcome(come_back(client, sent, idp))["reason"] == "expired"


def test_the_player_declining_at_the_provider_is_not_a_failure_of_ours(client, idp):
    sent = go_to_provider(client, idp)
    answer = client.get("/api/external/fake/callback", params={"error": "access_denied", "state": sent["state"]}, follow_redirects=False)
    assert outcome(answer) == {"external": "error", "reason": "denied"}


def test_other_provider_errors_and_a_missing_code_are_failures(client, idp):
    sent = go_to_provider(client, idp)
    answer = client.get("/api/external/fake/callback", params={"error": "server_error", "state": sent["state"]}, follow_redirects=False)
    assert outcome(answer)["reason"] == "failed"
    sent = go_to_provider(client, idp)
    answer = client.get("/api/external/fake/callback", params={"state": sent["state"]}, follow_redirects=False)
    assert outcome(answer)["reason"] == "failed"


def test_a_code_the_provider_refuses_is_a_failure(client, idp):
    sent = go_to_provider(client, idp)
    idp.token_status = 400
    assert outcome(come_back(client, sent, idp)) == {"external": "error", "reason": "failed"}


@pytest.mark.parametrize(
    "changes",
    [
        {"iss": "https://evil.example"},
        {"aud": "someone-else"},
        {"aud": ["client-1", "other"]},
        {"exp": 1},
        {"nonce": "not-this-one"},
        {"sub": ""},
        {"sub": None},
        {"exp": "soon"},
    ],
)
def test_an_id_token_that_does_not_check_out_is_refused(client, idp, changes):
    sent = go_to_provider(client, idp)
    idp.claims = claims(**{"nonce": sent["nonce"], **changes})
    answer = client.get("/api/external/fake/callback", params={"code": "c", "state": sent["state"]}, follow_redirects=False)
    assert outcome(answer) == {"external": "error", "reason": "failed"}
    assert client.get("/api/external/pending").json() == {"pending": None}


def test_a_token_for_several_audiences_needs_our_client_as_the_authorized_party(client, idp):
    sent = go_to_provider(client, idp)
    assert outcome(come_back(client, sent, idp, aud=["client-1", "other"], azp="client-1"))["external"] == "finish"


def test_a_token_that_expired_a_moment_ago_is_forgiven_a_stepped_back_clock(client, idp):
    sent = go_to_provider(client, idp)
    assert outcome(come_back(client, sent, idp, exp=int(wallclock.timestamp()) - 30))["external"] == "finish"


def test_a_token_that_is_not_a_token_is_a_failure(client, idp):
    sent = go_to_provider(client, idp)
    client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"id_token": "garbage"})))
    answer = client.get("/api/external/fake/callback", params={"code": "c", "state": sent["state"]}, follow_redirects=False)
    assert outcome(answer)["reason"] == "failed"


def test_a_microsoft_style_issuer_names_the_tenant_from_the_token(client, idp):
    client.app.state.settings.external_providers = [provider(issuer="https://login.test/{tenantid}/v2.0")]
    sent = go_to_provider(client, idp)
    ok = come_back(client, sent, idp, iss="https://login.test/tenant-9/v2.0", tid="tenant-9")
    assert outcome(ok)["external"] == "finish"
    sent = go_to_provider(client, idp)
    wrong = come_back(client, sent, idp, iss="https://login.test/tenant-8/v2.0", tid="tenant-9")
    assert outcome(wrong)["reason"] == "failed"


def test_the_callback_is_rate_limited_by_address(client):
    for _ in range(ratelimit.EXTERNAL_CALLBACK_BY_ADDRESS.attempts):
        client.get("/api/external/fake/callback", params={"code": "c", "state": "s"}, follow_redirects=False)
    assert client.get("/api/external/fake/callback", params={"code": "c", "state": "s"}, follow_redirects=False).status_code == 429


# --- two-factor login ------------------------------------------------------------------------------------------------------

def turn_on_two_factor(client, mailbox, headers, later):
    secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    expect(client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers), 202)
    token = re.search(r"token=([A-Za-z0-9_.-]+)", mailbox.last_to(EMAIL).body).group(1)
    codes = expect(client.post("/api/2fa/confirm", json={"token": token}, headers=headers), 200).json()["recovery_codes"]
    later(30)
    return secret, codes


@pytest.fixture
def protected_with_codes(client, idp, mailbox, later):
    """A player who signed up through the provider and turned two-factor login on, logged out again: (secret, recovery codes)."""
    headers = new_player(client, idp)
    made = turn_on_two_factor(client, mailbox, headers, later)
    client.cookies.clear()
    later(60)
    return made


@pytest.fixture
def protected(protected_with_codes):
    return protected_with_codes[0]


def test_two_factor_login_applies_to_the_provider_sign_in_too(client, idp, protected):
    assert outcome(sign_in(client, idp)) == {"external": "twofa"}
    assert client.get("/api/session").json()["account"] is None, "the provider is not enough"
    answer = expect(client.post("/api/external/2fa", json={"code": code_now(protected)}), 200)
    assert answer.json()["username"] == "Kim" and answer.json()["csrf_token"]
    assert client.get("/api/me").json()["username"] == "Kim"


def test_a_wrong_code_does_not_log_in_and_a_used_code_does_not_work_twice(client, idp, protected, later):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    assert client.post("/api/external/2fa", json={"code": "000000"}).status_code == 401
    assert client.get("/api/session").json()["account"] is None
    good = code_now(protected)
    expect(client.post("/api/external/2fa", json={"code": good}), 200)
    client.cookies.clear()
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    assert client.post("/api/external/2fa", json={"code": good}).status_code == 401, "each code works once"


def test_a_recovery_code_does_for_the_second_step_once(client, idp, protected_with_codes):
    _, codes = protected_with_codes
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    expect(client.post("/api/external/2fa", json={"code": codes[0]}), 200)
    client.cookies.clear()
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    assert client.post("/api/external/2fa", json={"code": codes[0]}).status_code == 401


def test_the_second_step_needs_the_first(client):
    assert client.post("/api/external/2fa", json={"code": "123456"}).status_code == 409


def test_guessing_codes_is_rate_limited_for_the_account(client, idp, protected):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    for _ in range(ratelimit.TWOFA_CODE_BY_ACCOUNT.attempts):
        client.post("/api/external/2fa", json={"code": "000000"})
    assert client.post("/api/external/2fa", json={"code": code_now(protected)}).status_code == 429


def test_a_password_reset_in_between_ends_the_waiting_sign_in(client, idp, protected, mailbox):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    in_app_db(client, lambda db: _bump_version(db))
    assert client.post("/api/external/2fa", json={"code": code_now(protected)}).status_code == 409


async def _bump_version(db):
    from sqlalchemy import select

    from terraforma.models import Account

    account = await db.scalar(select(Account).where(Account.username == "Kim"))
    account.session_version += 1


# --- linking ---------------------------------------------------------------------------------------------------------------

@pytest.fixture
def mike(client):
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    answer = client.post("/api/login", json={"username": "Mike", "password": PASSWORD})
    return {"X-CSRF-Token": answer.json()["csrf_token"]}


def link_it(client, idp, headers, **claim_changes):
    answer = expect(client.post("/api/external/fake/link", json={}, headers=headers), 200)
    return come_back(client, browse(answer, idp), idp, **claim_changes)


def test_a_logged_in_player_links_a_provider_explicitly(client, idp, mike):
    assert outcome(link_it(client, idp, mike)) == {"external": "linked"}
    assert identities_in_db(client) == [("fake", SUBJECT, EMAIL)], "the provider's address is kept, not used for the account"
    assert account_named(client, "Mike").email == "mike@example.com"
    assert client.get("/api/external/identities").json()["has_password"] is True
    assert client.get("/api/me").json()["username"] == "Mike", "still logged in as the same account"


def test_the_linked_provider_then_signs_in_to_that_account(client, idp, mike):
    link_it(client, idp, mike)
    client.cookies.clear()
    assert outcome(sign_in(client, idp)) == {"external": "done"}
    assert client.get("/api/me").json()["username"] == "Mike"


def test_linking_needs_a_login_and_the_csrf_token(client):
    assert client.post("/api/external/fake/link", json={}).status_code == 401
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    client.post("/api/login", json={"username": "Mike", "password": PASSWORD})
    assert client.post("/api/external/fake/link", json={}).status_code == 403


def test_a_provider_account_linked_to_someone_else_is_refused(client, idp, mike):
    new_player(client, idp)
    client.cookies.clear()
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    assert outcome(link_it(client, idp, headers)) == {"external": "error", "reason": "taken"}
    assert [each[1] for each in identities_in_db(client)] == [SUBJECT]
    assert account_named(client, "Kim") is not None


def test_one_account_links_a_provider_once(client, idp, mike):
    link_it(client, idp, mike)
    assert client.post("/api/external/fake/link", json={}, headers=mike).status_code == 409


def test_a_link_started_by_one_player_cannot_be_finished_by_another_login(client, idp, mike):
    answer = expect(client.post("/api/external/fake/link", json={}, headers=mike), 200)
    sent = browse(answer, idp)
    client.post("/api/logout", headers=mike)
    assert outcome(come_back(client, sent, idp))["reason"] in {"expired"}
    assert identities_in_db(client) == []


def test_linking_with_two_factor_on_needs_a_code(client, idp, protected, later):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    headers = {"X-CSRF-Token": expect(client.post("/api/external/2fa", json={"code": code_now(protected)}), 200).json()["csrf_token"]}
    client.app.state.settings.external_providers = [provider(), provider(name="other", label="Other")]
    assert client.post("/api/external/other/link", json={}, headers=headers).status_code == 401
    later(120)
    expect(client.post("/api/external/other/link", json={"code": code_now(protected)}, headers=headers), 200)


# --- unlinking and the last way in -----------------------------------------------------------------------------------------

def test_unlinking_works_when_another_way_in_remains(client, idp, mike):
    link_it(client, idp, mike)
    expect(client.post("/api/external/unlink", json={"provider": "fake"}, headers=mike), 200)
    assert identities_in_db(client) == []
    assert client.get("/api/external/identities").json()["identities"] == []


def test_the_last_way_in_is_never_removed(client, idp):
    headers = new_player(client, idp)
    answer = client.post("/api/external/unlink", json={"provider": "fake"}, headers=headers)
    assert answer.status_code == 409 and "password" in answer.json()["detail"]
    assert len(identities_in_db(client)) == 1


def test_setting_a_password_first_then_allows_unlinking(client, idp):
    headers = new_player(client, idp)
    assert client.post("/api/external/password", json={"password": "short"}, headers=headers).status_code == 422
    expect(client.post("/api/external/password", json={"password": PASSWORD}, headers=headers), 200)
    assert client.get("/api/external/identities").json()["has_password"] is True
    expect(client.post("/api/external/unlink", json={"provider": "fake"}, headers=headers), 200)
    client.cookies.clear()
    assert client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).status_code == 200


def test_an_account_that_has_a_password_cannot_use_the_first_password_call(client, mike):
    assert client.post("/api/external/password", json={"password": "another long password"}, headers=mike).status_code == 409


def test_unlinking_something_not_linked_is_not_found(client, mike):
    assert client.post("/api/external/unlink", json={"provider": "fake"}, headers=mike).status_code == 404


def test_unlinking_and_the_first_password_need_the_csrf_token(client, idp, mike):
    link_it(client, idp, mike)
    assert client.post("/api/external/unlink", json={"provider": "fake"}).status_code == 403
    assert client.post("/api/external/password", json={"password": PASSWORD}).status_code == 403


def test_unlinking_with_two_factor_on_needs_a_fresh_code(client, idp, protected, later):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    headers = {"X-CSRF-Token": expect(client.post("/api/external/2fa", json={"code": code_now(protected)}), 200).json()["csrf_token"]}
    later(120)
    expect(client.post("/api/external/password", json={"password": PASSWORD, "code": code_now(protected)}, headers=headers), 200)
    assert client.post("/api/external/unlink", json={"provider": "fake"}, headers=headers).status_code == 401
    assert client.post("/api/external/unlink", json={"provider": "fake", "code": "000000"}, headers=headers).status_code == 401
    later(240)
    expect(client.post("/api/external/unlink", json={"provider": "fake", "code": code_now(protected)}, headers=headers), 200)


def test_the_first_password_with_two_factor_on_needs_a_code(client, idp, protected):
    assert outcome(sign_in(client, idp))["external"] == "twofa"
    headers = {"X-CSRF-Token": expect(client.post("/api/external/2fa", json={"code": code_now(protected)}), 200).json()["csrf_token"]}
    assert client.post("/api/external/password", json={"password": PASSWORD}, headers=headers).status_code == 401


# --- the rest --------------------------------------------------------------------------------------------------------------

def test_a_password_reset_gives_a_provider_only_account_a_password(client, idp, mailbox):
    new_player(client, idp)
    client.cookies.clear()
    client.post("/api/password-reset", json={"email": EMAIL})
    token = re.search(r"token=([A-Za-z0-9_.-]+)", mailbox.last_to(EMAIL).body).group(1)
    expect(client.post("/api/password-reset/complete", json={"token": token, "password": PASSWORD}), 200)
    assert client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).status_code == 200


def test_the_callback_takes_no_unexpected_body_and_the_calls_are_strict(client, mike):
    assert client.post("/api/external/unlink", json={"provider": "fake", "extra": 1}, headers=mike).status_code == 422
    assert client.post("/api/external/finish", json={"username": "Kim", "handle": "Sparrow", "extra": 1}).status_code == 422


def test_logs_do_not_carry_the_state_or_the_code():
    from terraforma import logscrub

    line = 'GET /api/external/google/callback?state=abc123&code=4%2F0AfJohXn&scope=email HTTP/1.1" 303'
    scrubbed = logscrub.scrub(line)
    assert "abc123" not in scrubbed and "4%2F0AfJohXn" not in scrubbed


# --- security keys and remembered browsers with the provider sign-in ------------------------------------------------------------

def keyed_player(client, idp, mailbox):
    """A player who signed up through the provider and whose only second factor is a key, logged out: the key."""
    from .authenticator import SoftKey

    headers = new_player(client, idp)
    key = SoftKey()
    options = expect(client.post("/api/2fa/webauthn/options", json={}, headers=headers), 200).json()["options"]
    expect(client.post("/api/2fa/webauthn/register", json={"credential": key.create(options)}, headers=headers), 202)
    token = re.search(r"token=([A-Za-z0-9_.-]+)", mailbox.last_to(EMAIL).body).group(1)
    expect(client.post("/api/2fa/confirm", json={"token": token}, headers=headers), 200)
    client.cookies.clear()
    return key


def test_a_security_key_finishes_a_provider_sign_in(client, idp, mailbox, later):
    key = keyed_player(client, idp, mailbox)
    later(60)
    assert outcome(sign_in(client, idp)) == {"external": "twofa"}
    asked = expect(client.get("/api/external/2fa"), 200).json()
    assert asked["methods"] == ["key"] and asked["webauthn"]["rpId"] == "game.test"
    assert client.get("/api/session").json()["account"] is None
    answer = expect(client.post("/api/external/2fa", json={"assertion": key.get(asked["webauthn"])}), 200).json()
    assert answer["username"] == "Kim" and answer["csrf_token"] and "only one" in answer["note"]
    assert client.get("/api/session").json()["account"]["username"] == "Kim"


def test_a_provider_sign_in_second_step_refuses_a_wrong_or_replayed_answer(client, idp, mailbox, later):
    key = keyed_player(client, idp, mailbox)
    later(60)
    assert outcome(sign_in(client, idp)) == {"external": "twofa"}
    asked = expect(client.get("/api/external/2fa"), 200).json()
    answer = key.get(asked["webauthn"])
    assert client.post("/api/external/2fa", json={"assertion": answer, "code": "000000"}).status_code == 422, "one way or the other"
    expect(client.post("/api/external/2fa", json={"assertion": answer}), 200)
    client.cookies.clear()
    assert client.get("/api/external/2fa").status_code == 409, "no sign-in waiting"
    assert client.post("/api/external/2fa", json={"assertion": answer}).status_code == 409


def test_a_remembered_browser_skips_the_second_factor_of_a_provider_sign_in(client, idp, mailbox, later):
    key = keyed_player(client, idp, mailbox)
    later(60)
    assert outcome(sign_in(client, idp)) == {"external": "twofa"}
    asked = expect(client.get("/api/external/2fa"), 200).json()
    expect(client.post("/api/external/2fa", json={"assertion": key.get(asked["webauthn"]), "remember": True}), 200)
    cookies = {cookie.name: cookie for cookie in client.cookies.jar if cookie.name.startswith("tf_remember_")}
    assert len(cookies) == 1
    client.post("/api/logout", headers={"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]})
    later(3600)
    assert outcome(sign_in(client, idp)) == {"external": "done"}
    assert client.get("/api/session").json()["account"]["username"] == "Kim"
