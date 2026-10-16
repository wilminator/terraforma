"""The engine as a closed OpenID Connect provider: who may use it, the code flow with PKCE, the tokens, and what a site learns."""

import base64
import hashlib
import secrets
from contextlib import contextmanager
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import pytest
from pydantic import ValidationError

from terraforma import housekeeping, keys, wallclock
from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account, set_handle
from terraforma.keys import KeyRing
from terraforma.oidc import signing
from terraforma.oidc.models import OidcCode, OidcToken
from terraforma.settings import OidcClient, Settings
from terraforma.testing import in_app_db

from .helpers import expect

pytestmark = pytest.mark.accounts

PASSWORD = "correct horse battery"
STORE = "store"
STORE_SECRET = "s" * 40
STORE_REDIRECT = "https://store.example/callback"
OTHER = "wiki"
OTHER_SECRET = "w" * 40
OTHER_REDIRECT = "https://wiki.example/back"
ISSUER = "http://game.test"


def clients():
    return [
        OidcClient(client_id=STORE, client_secret=STORE_SECRET, redirect_uris=[STORE_REDIRECT, "http://localhost:3000/cb"], name="Store"),
        OidcClient(client_id=OTHER, client_secret=OTHER_SECRET, redirect_uris=[OTHER_REDIRECT]),
    ]


def turn_on(client):
    client.app.state.settings = client.app.state.settings.model_copy(update={"oidc_clients": clients()})


@pytest.fixture
def site(app_client):
    """The app with the provider on for two sites, and Mike, who has a handle, signed in."""
    turn_on(app_client)
    in_app_db(app_client, lambda db: make_player(db, "Mike", "mike@example.com", "Sir Mike"))
    expect(app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200)
    return app_client


async def make_player(db, username, email, handle=None):
    account = await create_account(db, username, PASSWORD, email=email, confirmed=True)
    if handle:
        await set_handle(db, account, handle)
    return account


def pkce():
    verifier = secrets.token_urlsafe(48)
    return verifier, base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def ask(challenge, **changes):
    asked = {
        "response_type": "code",
        "client_id": STORE,
        "redirect_uri": STORE_REDIRECT,
        "scope": "openid profile",
        "state": "abc",
        "nonce": "n-0S6",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    asked.update(changes)
    return {name: value for name, value in asked.items() if value is not None}


def authorize(client, challenge, **changes):
    return client.get("/oidc/authorize?" + urlencode(ask(challenge, **changes)), follow_redirects=False)


def sent_to(response):
    """Where an answer sends the browser: the address without its query, and the query's parameters (one value each)."""
    assert response.status_code == 303, response.text
    parts = urlsplit(response.headers["location"])
    return f"{parts.scheme}://{parts.netloc}{parts.path}", {name: values[0] for name, values in parse_qs(parts.query).items()}


def get_code(client, **changes):
    verifier, challenge = pkce()
    _, query = sent_to(authorize(client, challenge, **changes))
    assert "code" in query, query
    return query["code"], verifier


@contextmanager
def as_site(client):
    """The site's own server calls without the player's cookie: no login of the player's is used (or needed)."""
    kept = dict(client.cookies)
    client.cookies.clear()
    try:
        yield client
    finally:
        client.cookies.clear()
        for name, value in kept.items():
            client.cookies.set(name, value)


def basic(identifier=STORE, secret=STORE_SECRET):
    return {"Authorization": "Basic " + base64.b64encode(f"{identifier}:{secret}".encode()).decode()}


def trade(client, code, verifier, *, redirect_uri=STORE_REDIRECT, headers=None, **form):
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier, **form}
    with as_site(client):
        return client.post("/oidc/token", data=body, headers=basic() if headers is None else headers)


def tokens(client, **changes):
    code, verifier = get_code(client, **changes)
    return expect(trade(client, code, verifier), 200).json()


def userinfo(client, access_token, method="GET"):
    with as_site(client):
        return client.request(method, "/oidc/userinfo", headers={"Authorization": f"Bearer {access_token}"})


def verified(client, id_token, audience=STORE):
    """The ID token's claims, checked against the published keys (its expiry is on the wall clock the tests hold still, not the real one)."""
    with as_site(client):
        key_set = expect(client.get("/oidc/jwks"), 200).json()
    key = next(each for each in key_set["keys"] if each["kid"] == jwt.get_unverified_header(id_token)["kid"])
    return jwt.decode(id_token, jwt.PyJWK.from_dict(key).key, algorithms=["ES256"], audience=audience, issuer=ISSUER, options={"verify_exp": False})


# --- who may use it ---------------------------------------------------------------------------------------------------

def test_with_no_site_listed_nothing_exists(app_client):
    for method, address in [("get", "/.well-known/openid-configuration"), ("get", "/oidc/jwks"), ("get", "/oidc/authorize"), ("get", "/oidc/resume?request=x"), ("post", "/oidc/token"), ("get", "/oidc/userinfo")]:
        assert getattr(app_client, method)(address).status_code == 404, address


def test_a_site_needs_a_long_secret_a_safe_address_and_a_name_of_its_own():
    good = {"client_id": "store", "client_secret": "s" * 32, "redirect_uris": ["https://store.example/cb"]}
    OidcClient(**good)
    OidcClient(**{**good, "redirect_uris": ["http://localhost:3000/cb", "http://127.0.0.1/cb", "http://[::1]:8080/cb"]})
    for change in [
        {"client_secret": "s" * 31},
        {"client_id": "no"},
        {"client_id": "has space"},
        {"redirect_uris": []},
        {"redirect_uris": ["http://store.example/cb"]},
        {"redirect_uris": ["https://store.example/cb#top"]},
        {"redirect_uris": ["https://user@store.example/cb"]},
        {"redirect_uris": ["/relative"]},
        {"redirect_uris": ["javascript:alert(1)"]},
        {"misspelled": 1},
    ]:
        with pytest.raises(ValidationError):
            OidcClient(**{**good, **change})
    settings = {"session_secret": "x" * 32}
    assert Settings(**settings).oidc_clients == [], "off by default"
    with pytest.raises(ValidationError, match="different"):
        Settings(**settings, oidc_clients=[good, good])


def test_the_discovery_document_describes_only_what_is_built(site):
    with as_site(site):
        document = expect(site.get("/.well-known/openid-configuration"), 200).json()
    assert document["issuer"] == ISSUER
    assert document["authorization_endpoint"] == f"{ISSUER}/oidc/authorize"
    assert document["token_endpoint"] == f"{ISSUER}/oidc/token"
    assert document["userinfo_endpoint"] == f"{ISSUER}/oidc/userinfo"
    assert document["jwks_uri"] == f"{ISSUER}/oidc/jwks"
    assert document["response_types_supported"] == ["code"]
    assert document["grant_types_supported"] == ["authorization_code"]
    assert document["code_challenge_methods_supported"] == ["S256"]
    assert document["id_token_signing_alg_values_supported"] == ["ES256"]
    assert document["subject_types_supported"] == ["pairwise"]
    assert set(document["scopes_supported"]) == {"openid", "profile"}
    assert "email" not in document["claims_supported"]


# --- the flow ---------------------------------------------------------------------------------------------------------

def test_a_signed_in_player_is_sent_back_with_a_code_and_the_site_learns_a_subject_and_the_handle(site):
    verifier, challenge = pkce()
    answer = authorize(site, challenge)
    address, query = sent_to(answer)
    assert address == STORE_REDIRECT
    assert query["state"] == "abc" and query["iss"] == ISSUER and query["code"]
    assert answer.headers["referrer-policy"] == "no-referrer" and answer.headers["cache-control"] == "no-store"

    issued = expect(trade(site, query["code"], verifier), 200).json()
    assert issued["token_type"] == "Bearer" and issued["expires_in"] == 3600 and issued["scope"] == "openid profile"
    claims = verified(site, issued["id_token"])
    assert claims["nonce"] == "n-0S6" and claims["aud"] == STORE and claims["iss"] == ISSUER
    assert claims["exp"] - claims["iat"] == 600
    assert claims["preferred_username"] == "Sir Mike"
    assert set(claims) == {"iss", "sub", "aud", "iat", "exp", "nonce", "preferred_username"}, "nothing else about the player"
    text = str(claims) + str(issued)
    assert "Mike@" not in text and "mike@example.com" not in text and "'Mike'" not in text, "neither the email nor the username"

    answer = expect(userinfo(site, issued["access_token"]), 200)
    assert answer.json() == {"sub": claims["sub"], "preferred_username": "Sir Mike"}
    assert expect(userinfo(site, issued["access_token"], "POST"), 200).json() == answer.json()
    assert answer.headers["cache-control"] == "no-store"


def test_a_site_may_sign_in_with_the_secret_in_the_form_instead_of_the_header(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, headers={}, client_id=STORE, client_secret=STORE_SECRET)
    assert "id_token" in expect(answer, 200).json()


def test_a_redirect_address_with_a_query_keeps_it(site):
    site.app.state.settings = site.app.state.settings.model_copy(
        update={"oidc_clients": [OidcClient(client_id=STORE, client_secret=STORE_SECRET, redirect_uris=["https://store.example/cb?site=1"])]}
    )
    _, challenge = pkce()
    _, query = sent_to(authorize(site, challenge, redirect_uri="https://store.example/cb?site=1"))
    assert query["site"] == "1" and "code" in query


def test_the_subject_is_opaque_stable_and_different_for_each_site_and_each_player(site):
    first = verified(site, tokens(site)["id_token"])["sub"]
    again = verified(site, tokens(site)["id_token"])["sub"]
    other_site = verified(site, _other_site_tokens(site)["id_token"], audience=OTHER)["sub"]
    assert first == again, "stable"
    assert first != other_site, "two sites cannot tell they have the same player"
    assert len(first) == 43 and "mike" not in first.casefold()

    site.cookies.clear()
    in_app_db(site, lambda db: make_player(db, "Anna", "anna@example.com", "Anna B"))
    expect(site.post("/api/login", json={"username": "Anna", "password": PASSWORD}), 200)
    assert verified(site, tokens(site)["id_token"])["sub"] != first


def _other_site_tokens(client):
    verifier, challenge = pkce()
    _, query = sent_to(authorize(client, challenge, client_id=OTHER, redirect_uri=OTHER_REDIRECT))
    return expect(trade(client, query["code"], verifier, headers=basic(OTHER, OTHER_SECRET), redirect_uri=OTHER_REDIRECT), 200).json()


def test_the_profile_scope_is_what_gives_the_handle_and_other_scopes_give_nothing(site):
    plain = tokens(site, scope="openid")
    assert plain["scope"] == "openid" and "preferred_username" not in verified(site, plain["id_token"])
    assert set(userinfo(site, plain["access_token"]).json()) == {"sub"}
    wide = tokens(site, scope="email openid offline_access profile")
    assert wide["scope"] == "openid profile", "scopes that are not offered are ignored, as the standard says"
    assert "email" not in verified(site, wide["id_token"])


def test_without_a_nonce_the_id_token_has_none(site):
    assert "nonce" not in verified(site, tokens(site, nonce=None)["id_token"])


# --- the login in the middle ------------------------------------------------------------------------------------------

def test_a_player_who_is_not_signed_in_logs_in_and_the_request_waits_for_them(site):
    site.cookies.clear()
    verifier, challenge = pkce()
    answer = authorize(site, challenge)
    assert answer.status_code == 303
    parts = urlsplit(answer.headers["location"])
    assert parts.path == "/account/"
    query = parse_qs(parts.query)
    assert query["next"] == ["oidc"] and len(query["request"]) == 1
    waiting = query["request"][0]

    expect(site.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200)
    address, back = sent_to(site.get("/oidc/resume", params={"request": waiting}, follow_redirects=False))
    assert address == STORE_REDIRECT and back["state"] == "abc"
    assert "id_token" in expect(trade(site, back["code"], verifier), 200).json(), "the same PKCE challenge, so the same verifier"


def test_resuming_while_still_logged_out_goes_back_to_the_login_and_a_bad_or_old_request_is_refused(site, later):
    site.cookies.clear()
    _, challenge = pkce()
    waiting = parse_qs(urlsplit(authorize(site, challenge).headers["location"]).query)["request"][0]
    answer = site.get("/oidc/resume", params={"request": waiting}, follow_redirects=False)
    assert urlsplit(answer.headers["location"]).path == "/account/"

    expect(site.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200)
    forged = waiting[:-3] + ("AAA" if not waiting.endswith("AAA") else "BBB")
    assert expect(site.get("/oidc/resume", params={"request": forged}, follow_redirects=False), 400).json()["error"] == "invalid_request"
    later(11 * 60)
    assert expect(site.get("/oidc/resume", params={"request": waiting}, follow_redirects=False), 400).json()["error"] == "invalid_request"


def test_a_resumed_request_is_checked_again_so_removing_the_site_stops_it(site):
    site.cookies.clear()
    _, challenge = pkce()
    waiting = parse_qs(urlsplit(authorize(site, challenge).headers["location"]).query)["request"][0]
    expect(site.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200)
    site.app.state.settings = site.app.state.settings.model_copy(update={"oidc_clients": clients()[1:]})
    assert expect(site.get("/oidc/resume", params={"request": waiting}, follow_redirects=False), 400).json()["error"] == "invalid_request"


def test_a_request_for_another_purpose_cannot_be_passed_off_as_a_waiting_sign_in(site):
    forged = site.app.state.tokens.make("password-reset", 1, client_id=STORE, redirect_uri=STORE_REDIRECT)
    assert expect(site.get("/oidc/resume", params={"request": forged}, follow_redirects=False), 400).json()["error"] == "invalid_request"


def test_prompt_none_never_shows_a_login_and_says_so(site):
    site.cookies.clear()
    _, challenge = pkce()
    address, query = sent_to(authorize(site, challenge, prompt="none"))
    assert address == STORE_REDIRECT and query["error"] == "login_required" and query["state"] == "abc" and "code" not in query


def test_prompt_none_with_a_login_gives_a_code_straight_away(site):
    _, query = sent_to(authorize(site, pkce()[1], prompt="none"))
    assert "code" in query


def test_prompt_login_makes_a_signed_in_player_log_in_again(site):
    answer = authorize(site, pkce()[1], prompt="login")
    assert urlsplit(answer.headers["location"]).path == "/account/"
    assert site.get("/api/me").status_code == 401, "they are signed out until they log in"
    waiting = parse_qs(urlsplit(answer.headers["location"]).query)["request"][0]
    expect(site.post("/api/login", json={"username": "Mike", "password": PASSWORD}), 200)
    _, query = sent_to(site.get("/oidc/resume", params={"request": waiting}, follow_redirects=False))
    assert "code" in query, "and then on, without being sent round again"


def test_a_player_with_no_public_handle_is_refused_and_told_why(site):
    site.cookies.clear()
    in_app_db(site, lambda db: make_player(db, "Anna", "anna@example.com"))
    expect(site.post("/api/login", json={"username": "Anna", "password": PASSWORD}), 200)
    address, query = sent_to(authorize(site, pkce()[1]))
    assert address == STORE_REDIRECT and query["error"] == "access_denied" and query["state"] == "abc"
    assert "handle" in query["error_description"]


# --- what is refused before anything is sent to an address ----------------------------------------------------------------

@pytest.mark.parametrize(
    "changes",
    [
        {"client_id": "stranger"},
        {"client_id": ""},
        {"redirect_uri": "https://evil.example/callback"},
        {"redirect_uri": "https://store.example/callback/"},
        {"redirect_uri": "https://store.example/callback?x=1"},
        {"redirect_uri": "https://STORE.example/callback"},
        {"redirect_uri": "https://store.example/call"},
        {"redirect_uri": OTHER_REDIRECT},
        {"redirect_uri": None},
    ],
)
def test_an_unknown_site_or_an_address_not_listed_for_it_is_shown_an_error_and_never_redirected(site, changes):
    answer = authorize(site, pkce()[1], **changes)
    assert answer.status_code == 400 and "location" not in answer.headers
    assert answer.json()["error"] == "invalid_request"


def test_a_parameter_sent_twice_is_refused(site):
    query = urlencode(ask(pkce()[1])) + "&state=again"
    answer = site.get("/oidc/authorize?" + query, follow_redirects=False)
    assert answer.status_code == 400 and answer.json()["error"] == "invalid_request"


# --- what is sent back as an error ------------------------------------------------------------------------------------------

@pytest.mark.parametrize(
    "changes, error",
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"response_type": "id_token"}, "unsupported_response_type"),
        ({"response_type": ""}, "unsupported_response_type"),
        ({"scope": "profile"}, "invalid_scope"),
        ({"scope": ""}, "invalid_scope"),
        ({"code_challenge": None}, "invalid_request"),
        ({"code_challenge": "short"}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge_method": None}, "invalid_request"),
        ({"prompt": "none login"}, "invalid_request"),
        ({"prompt": "create"}, "invalid_request"),
    ],
)
def test_a_request_that_is_not_the_code_flow_with_pkce_goes_back_with_the_standards_error(site, changes, error):
    _, challenge = pkce()
    address, query = sent_to(authorize(site, challenge, **changes))
    assert address == STORE_REDIRECT and query["error"] == error and query["state"] == "abc" and query["iss"] == ISSUER
    assert "code" not in query


def test_extra_parameters_are_ignored(site):
    _, challenge = pkce()
    _, query = sent_to(authorize(site, challenge, ui_locales="fr", login_hint="x", max_age="0"))
    assert "code" in query


# --- the token call ---------------------------------------------------------------------------------------------------------

def test_the_token_call_needs_the_right_site_and_secret(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, headers=basic(STORE, "x" * 40))
    assert answer.status_code == 401 and answer.json()["error"] == "invalid_client"
    assert answer.headers["www-authenticate"].startswith("Basic")
    assert answer.headers["cache-control"] == "no-store"
    assert trade(site, code, verifier, headers=basic("stranger", STORE_SECRET)).status_code == 401
    assert trade(site, code, verifier, headers=basic(OTHER, STORE_SECRET)).status_code == 401, "another site's secret is not this one's"
    assert trade(site, code, verifier, headers={}).status_code == 401, "no credentials"
    assert trade(site, code, verifier, headers={"Authorization": "Basic !!!"}).status_code == 401
    assert trade(site, code, verifier, headers={}, client_id=STORE, client_secret="nope").json()["error"] == "invalid_client"
    assert "id_token" in expect(trade(site, code, verifier), 200).json(), "the failed calls did not spend the code"


def test_credentials_in_the_header_and_the_form_together_are_refused(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, client_id=STORE, client_secret=STORE_SECRET)
    assert answer.status_code == 400 and answer.json()["error"] == "invalid_request"


def test_the_token_call_takes_a_form_only_with_each_parameter_once(site):
    code, verifier = get_code(site)
    with as_site(site):
        sent_as_json = site.post("/oidc/token", json={"grant_type": "authorization_code"}, headers=basic())
        twice = site.post("/oidc/token", content=f"grant_type=authorization_code&grant_type=x&code={code}", headers={**basic(), "Content-Type": "application/x-www-form-urlencoded"})
        huge = site.post("/oidc/token", content=b"a=" + b"x" * 20000, headers={**basic(), "Content-Type": "application/x-www-form-urlencoded"})
        long_code = site.post("/oidc/token", data={"grant_type": "authorization_code", "code": "c" * 300}, headers=basic())
    for answer in (sent_as_json, twice, huge, long_code):
        assert answer.status_code == 400 and answer.json()["error"] == "invalid_request", answer.text


def test_only_the_code_grant_exists_and_it_needs_all_its_parameters(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, grant_type="refresh_token")
    assert answer.status_code == 400 and answer.json()["error"] == "unsupported_grant_type"
    assert trade(site, code, verifier, grant_type="password").json()["error"] == "unsupported_grant_type"
    for missing in ("code", "redirect_uri", "code_verifier"):
        body = {"grant_type": "authorization_code", "code": code, "redirect_uri": STORE_REDIRECT, "code_verifier": verifier}
        del body[missing]
        with as_site(site):
            answer = site.post("/oidc/token", data=body, headers=basic())
        assert answer.json()["error"] == "invalid_request", missing


def test_a_code_works_once_and_showing_it_again_revokes_what_it_made(site):
    code, verifier = get_code(site)
    issued = expect(trade(site, code, verifier), 200).json()
    assert expect(userinfo(site, issued["access_token"]), 200)
    again = trade(site, code, verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert userinfo(site, issued["access_token"]).status_code == 401, "the first trade's access token is revoked"


def test_a_code_belongs_to_the_site_it_was_made_for(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, headers=basic(OTHER, OTHER_SECRET), redirect_uri=OTHER_REDIRECT)
    assert answer.json()["error"] == "invalid_grant"
    assert "id_token" in expect(trade(site, code, verifier), 200).json(), "the other site's attempt did not spend it"


def test_the_redirect_address_must_be_the_one_the_sign_in_started_with(site):
    code, verifier = get_code(site)
    answer = trade(site, code, verifier, redirect_uri="http://localhost:3000/cb")
    assert answer.json()["error"] == "invalid_grant"
    assert trade(site, code, verifier).json()["error"] == "invalid_grant", "and a wrong try spends the code"


def test_the_verifier_must_match_the_challenge_and_a_wrong_one_spends_the_code(site):
    code, verifier = get_code(site)
    answer = trade(site, code, pkce()[0])
    assert answer.status_code == 400 and answer.json()["error"] == "invalid_grant"
    assert trade(site, code, verifier).json()["error"] == "invalid_grant"
    code, _ = get_code(site)
    assert trade(site, code, "short").json()["error"] == "invalid_grant"


def test_a_code_lasts_a_minute(site, later):
    code, verifier = get_code(site)
    later(61)
    assert trade(site, code, verifier).json()["error"] == "invalid_grant"
    code, verifier = get_code(site)
    later(61 + 59)
    assert "id_token" in expect(trade(site, code, verifier), 200).json()


def test_an_ended_login_ends_the_codes_and_tokens_made_from_it(site):
    code, verifier = get_code(site)
    issued = tokens(site)
    in_app_db(site, lambda db: _end_logins(db))
    assert trade(site, code, verifier).json()["error"] == "invalid_grant"
    assert userinfo(site, issued["access_token"]).status_code == 401, "a password reset ends the access to the store too"


async def _end_logins(db):
    from sqlalchemy import select

    from terraforma.models import Account

    account = await db.scalar(select(Account).where(Account.username == "Mike"))
    account.session_version += 1


# --- the userinfo call ------------------------------------------------------------------------------------------------------

def test_the_userinfo_call_needs_a_good_access_token_in_the_header(site, later):
    issued = tokens(site)
    with as_site(site):
        bare = site.get("/oidc/userinfo")
        wrong = site.get("/oidc/userinfo", headers={"Authorization": "Bearer nope"})
        in_address = site.get("/oidc/userinfo", params={"access_token": issued["access_token"]})
        as_basic = site.get("/oidc/userinfo", headers=basic())
    assert bare.status_code == 401 and bare.headers["www-authenticate"] == "Bearer"
    assert wrong.status_code == 401 and 'error="invalid_token"' in wrong.headers["www-authenticate"]
    assert in_address.status_code == 401 and as_basic.status_code == 401
    assert userinfo(site, issued["access_token"]).status_code == 200
    later(3601)
    assert userinfo(site, issued["access_token"]).status_code == 401, "an access token lasts an hour"


def test_the_handle_in_userinfo_is_the_current_one(site):
    issued = tokens(site)
    expect(site.post("/api/handle", json={"handle": "Lord Mike"}, headers={"X-CSRF-Token": site.get("/api/session").json()["csrf_token"]}), 200)
    assert userinfo(site, issued["access_token"]).json()["preferred_username"] == "Lord Mike"


def test_a_stored_code_or_token_is_only_a_hash(site):
    code, verifier = get_code(site)
    issued = expect(trade(site, code, verifier), 200).json()
    stored = in_app_db(site, _stored_values)
    assert code not in stored and issued["access_token"] not in stored
    assert hashlib.sha256(code.encode()).hexdigest() in stored


async def _stored_values(db):
    from sqlalchemy import select

    return " ".join([*(await db.scalars(select(OidcCode.code_hash))).all(), *(await db.scalars(select(OidcToken.token_hash))).all()])


# --- the keys ---------------------------------------------------------------------------------------------------------------

def test_the_signing_key_comes_from_the_slot_key_and_is_not_it(tmp_path):
    keys.new_key(tmp_path)
    ring = KeyRing.load(tmp_path)
    first = signing.signing_keys(ring)
    assert len(first) == 1 and first == signing.signing_keys(KeyRing.load(tmp_path)), "the same key on every server and every start"
    assert first[0].jwk["alg"] == "ES256" and first[0].jwk["use"] == "sig" and "d" not in first[0].jwk, "public parts only"
    assert ring.current.secret not in str(first[0].jwk).encode()
    other = tmp_path / "other"
    keys.new_key(other)
    assert signing.signing_keys(KeyRing.load(other))[0].kid != first[0].kid


def test_rotating_the_keys_rotates_the_signing_key_and_keeps_the_old_one_published(site, tmp_path):
    old = tokens(site)["id_token"]
    old_kid = jwt.get_unverified_header(old)["kid"]
    keys.shift_slots(site.app.state.settings.key_dir)
    site.app.state.keys = KeyRing.load(site.app.state.settings.key_dir)
    with as_site(site):
        published = [each["kid"] for each in expect(site.get("/oidc/jwks"), 200).json()["keys"]]
    new = tokens(site)["id_token"]
    new_kid = jwt.get_unverified_header(new)["kid"]
    assert published == [new_kid, old_kid], "the current key first, then the previous one"
    assert verified(site, old)["aud"] == STORE, "a token made just before the rotation still checks out"
    assert verified(site, new)["aud"] == STORE


# --- tidiness and limits ----------------------------------------------------------------------------------------------------

async def _row_counts(db):
    from sqlalchemy import func, select

    return await db.scalar(select(func.count()).select_from(OidcCode)), await db.scalar(select(func.count()).select_from(OidcToken))


def tidy(client) -> int:
    return in_app_db(client, lambda db: housekeeping.expired_oidc_codes_and_tokens(db, wallclock.timestamp(), client.app.state.settings))


def test_housekeeping_removes_codes_and_tokens_that_have_run_out_and_nothing_else(site, later):
    tokens(site)
    get_code(site)
    assert in_app_db(site, _row_counts) == (2, 1), "the spent code, the unspent one, and the access token"
    assert tidy(site) == 0
    later(61)
    assert tidy(site) == 2 and in_app_db(site, _row_counts) == (0, 1), "a spent code is kept until it expires, to know a replay"
    later(3601)
    assert tidy(site) == 1 and in_app_db(site, _row_counts) == (0, 0)


@pytest.mark.parametrize(
    "limit, call",
    [
        ("OIDC_AUTHORIZE_BY_ADDRESS", lambda client: client.get("/oidc/authorize")),
        ("OIDC_TOKEN_BY_ADDRESS", lambda client: client.post("/oidc/token", data={"grant_type": "x"}, headers=basic())),
        ("OIDC_USERINFO_BY_ADDRESS", lambda client: client.get("/oidc/userinfo")),
    ],
)
def test_every_call_is_rate_limited_by_address(site, monkeypatch, limit, call):
    monkeypatch.setattr(ratelimit, limit, ratelimit.Limit(getattr(ratelimit, limit).name, 3, 900))
    assert [call(site).status_code for _ in range(3)] != [429] * 3
    answer = call(site)
    assert answer.status_code == 429 and "Retry-After" in answer.headers
