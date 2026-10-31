"""The provider telling this site a person deleted their account there: a signed message, checked, then the person is unlinked and logged out."""

import time

import httpx2 as httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from terraforma.settings import ExternalProvider

from .helpers import expect
from .test_external_signin import ISSUER, SUBJECT, FakeProvider, identities_in_db, new_player, provider

pytestmark = pytest.mark.accounts

DELETED = f"{ISSUER}/events/account-deleted"
KEY = ec.generate_private_key(ec.SECP256R1())
OTHER_KEY = ec.generate_private_key(ec.SECP256R1())


def jwk(private, kid="k1") -> dict:
    return {**jwt.algorithms.ECAlgorithm.to_jwk(private.public_key(), as_dict=True), "kid": kid, "alg": "ES256", "use": "sig"}


class EventsProvider(FakeProvider):
    """The sign-in provider plus its published settings and key set."""

    def __init__(self):
        super().__init__()
        self.keys = [jwk(KEY)]
        self.keys_status = 200

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/authorize", "token_endpoint": f"{ISSUER}/token", "jwks_uri": f"{ISSUER}/jwks"})
        if request.url.path == "/jwks":
            return httpx.Response(self.keys_status, json={"keys": self.keys})
        return super().handle(request)


@pytest.fixture
def idp():
    return EventsProvider()


@pytest.fixture
def client(app_client, idp):
    # No endpoints given here: the key set's address is read from the provider's published settings.
    app_client.app.state.settings.external_providers = [events_provider()]
    app_client.app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
    return app_client


def events_provider(**changes) -> ExternalProvider:
    return ExternalProvider(name="fake", label="Fake", issuer=ISSUER, client_id="client-1", client_secret="client-secret-1", deletion_events=True, **changes)


def event(sub=SUBJECT, *, key=KEY, algorithm="ES256", headers=None, events=None, **changes) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "client-1", "iat": now, "exp": now + 300, "jti": "j1", "events": {DELETED: {"sub": sub}} if events is None else events, **changes}
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm=algorithm, headers={"kid": "k1", "typ": "secevent+jwt", **(headers or {})})


def post(client, token):
    return client.post("/api/external/fake/events", json={"token": token})


def logged_in(client) -> bool:
    return client.get("/api/external/identities").status_code == 200


# --- settings --------------------------------------------------------------------------------------------------------------

def test_deletion_events_are_off_by_default_and_only_for_openid_connect():
    assert provider().deletion_events is False
    events_provider()
    with pytest.raises(ValidationError):
        ExternalProvider(name="steam", label="Steam", kind="steam", deletion_events=True)
    with pytest.raises(ValidationError):
        provider(jwks_uri="http://idp.example.com/keys")
    # Endpoints given by hand leave nowhere to find the keys, so they must be given too.
    with pytest.raises(ValidationError):
        provider(deletion_events=True)
    provider(deletion_events=True, jwks_uri=f"{ISSUER}/jwks")


# --- the message ------------------------------------------------------------------------------------------------------------

def test_a_deletion_unlinks_the_person_and_logs_them_out(client, idp):
    new_player(client, idp)
    assert logged_in(client) and identities_in_db(client) == [("fake", SUBJECT, "kim@example.com")]
    assert expect(post(client, event()), 200).json() == {"ok": True}
    assert identities_in_db(client) == []
    assert not logged_in(client)


def test_an_unknown_person_gets_the_same_answer(client):
    assert expect(post(client, event("nobody")), 200).json() == {"ok": True}


def test_other_events_are_accepted_and_ignored(client, idp):
    new_player(client, idp)
    assert expect(post(client, event(events={f"{ISSUER}/events/supporter-changed": {"sub": SUBJECT}})), 200).json() == {"ok": True}
    assert expect(post(client, event(events={})), 200).json() == {"ok": True}
    assert logged_in(client) and len(identities_in_db(client)) == 1


def test_a_deletion_without_a_subject_is_refused(client):
    for bad in ({}, {"sub": 5}, {"sub": ""}, "x"):
        expect(post(client, event(events={DELETED: bad})), 400)


@pytest.mark.parametrize(
    "token",
    [
        event(key=OTHER_KEY),  # signed by someone else
        event(aud="client-2"),
        event(iss="https://evil.test"),
        event(exp=int(time.time()) - 3600),
        event(exp=None),
        event(jti=None),
        event(headers={"typ": "JWT"}),
        event(headers={"kid": "k2"}),
        "not-a-token",
        jwt.encode({"iss": ISSUER, "aud": "client-1", "exp": int(time.time()) + 300, "iat": 1, "events": {DELETED: {"sub": SUBJECT}}}, "a-shared-secret-of-at-least-32-bytes!", algorithm="HS256", headers={"kid": "k1", "typ": "secevent+jwt"}),
    ],
    ids=["other-key", "audience", "issuer", "expired", "no-expiry", "no-jti", "type", "unknown-key", "garbage", "shared-secret"],
)
def test_a_message_that_does_not_check_out_changes_nothing(client, idp, token):
    new_player(client, idp)
    expect(post(client, token), 400)
    assert logged_in(client) and len(identities_in_db(client)) == 1


def test_the_unsigned_algorithm_is_refused(client, idp):
    new_player(client, idp)
    token = jwt.encode({"iss": ISSUER, "aud": "client-1", "iat": 1, "exp": int(time.time()) + 300, "events": {DELETED: {"sub": SUBJECT}}}, None, algorithm="none", headers={"kid": "k1", "typ": "secevent+jwt"})
    expect(post(client, token), 400)
    assert logged_in(client)


def test_keys_that_cannot_be_fetched_refuse_the_message(client, idp):
    idp.keys_status = 500
    expect(post(client, event()), 400)


def test_a_rotated_key_is_found_by_its_id(client, idp):
    idp.keys = [jwk(OTHER_KEY, "k0"), jwk(KEY)]
    expect(post(client, event()), 200)


def test_a_provider_without_deletion_events_has_no_such_call(client):
    client.app.state.settings.external_providers = [events_provider().model_copy(update={"deletion_events": False})]
    expect(post(client, event()), 404)
    expect(client.post("/api/external/other/events", json={"token": event()}), 404)


def test_the_body_is_strict(client):
    expect(client.post("/api/external/fake/events", json={"token": event(), "extra": 1}), 422)
    expect(client.post("/api/external/fake/events", json={"token": 5}), 422)
    expect(client.post("/api/external/fake/events", json={"token": ""}), 422)


# --- the lookup ---------------------------------------------------------------------------------------------------------------

def lookup(client, token):
    return client.post("/api/external/fake/account-lookup", json={"token": token})


def asking(sub=SUBJECT, **changes) -> str:
    return _signed({"sub": sub, **changes})


def _signed(claims: dict) -> str:
    now = int(time.time())
    every = {"iss": ISSUER, "aud": "client-1", "iat": now, "exp": now + 300, "jti": "j2", **claims}
    return jwt.encode({k: v for k, v in every.items() if v is not None}, KEY, algorithm="ES256", headers={"kid": "k1", "typ": "secevent+jwt"})


def test_the_lookup_says_whether_the_person_has_an_account_here(client, idp):
    assert expect(lookup(client, asking()), 200).json() == {"exists": False}
    new_player(client, idp)
    assert expect(lookup(client, asking()), 200).json() == {"exists": True, "summary": "an account with 0 teams"}
    assert expect(lookup(client, asking("someone-else")), 200).json() == {"exists": False}


def test_the_lookup_changes_nothing(client, idp):
    new_player(client, idp)
    expect(lookup(client, asking()), 200)
    assert logged_in(client) and len(identities_in_db(client)) == 1


def test_a_lookup_that_does_not_check_out_is_refused(client):
    expect(lookup(client, asking(aud="client-2")), 400)
    expect(lookup(client, asking(jti=None)), 400)
    expect(lookup(client, asking(sub=None)), 400)
    expect(lookup(client, asking(sub=5)), 400)
    expect(lookup(client, "not-a-token"), 400)
    expect(client.post("/api/external/fake/account-lookup", json={"token": "x", "extra": 1}), 422)


def test_the_lookup_is_off_without_deletion_events(client):
    client.app.state.settings.external_providers = [events_provider().model_copy(update={"deletion_events": False})]
    expect(lookup(client, asking()), 404)
