"""Second factors beyond the app: security keys and passkeys (WebAuthn), recovery codes for any method, remembered browsers,
the game's note for a single method, and the admin's reset. The key is played by tests/authenticator.py."""

import json
import re
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import housekeeping, wallclock
from terraforma.accounts import ratelimit, remember, second, twofa
from terraforma.accounts.hooks import SignIn
from terraforma.accounts.service import create_account
from terraforma.game import Game
from terraforma.models import Account, RememberedBrowser, WebauthnKey
from terraforma.testing import in_app_db

from .authenticator import SoftKey
from .helpers import expect

PASSWORD = "correct horse battery"
EMAIL = "mike@example.com"


def token_in(mail) -> str:
    return re.search(r"token=([A-Za-z0-9_.-]+)", mail.body).group(1)


def code_now(secret: str) -> str:
    return twofa.code_at(secret, twofa.current_step())


@pytest.fixture
def mike(app_client):
    in_app_db(app_client, lambda db: create_account(db, "Mike", PASSWORD, email=EMAIL, confirmed=True))
    answer = app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD})
    return app_client, {"X-CSRF-Token": answer.json()["csrf_token"]}


def log_in(client, **extra):
    return client.post("/api/login", json={"username": "Mike", "password": PASSWORD, **extra})


def log_out(client, headers):
    client.post("/api/logout", headers=headers)


def account_row(client, username="Mike"):
    async def get(db):
        row = await db.scalar(select(Account).where(Account.username == username))
        db.expunge(row)
        return row

    return in_app_db(client, get)


def count_of(client, model):
    async def count(db):
        return await db.scalar(select(func.count()).select_from(model))

    return in_app_db(client, count)


def add_key(client, headers, mailbox, key=None, name="Blue key", proof=None, confirm=True):
    """Registers a key and follows the emailed link. Returns (key, the confirm answer, or the register answer without confirm)."""
    key = key or SoftKey()
    body = {"proof": proof} if proof else {}
    options = expect(client.post("/api/2fa/webauthn/options", json=body, headers=headers), 200).json()["options"]
    expect(client.post("/api/2fa/webauthn/register", json={"credential": key.create(options), "name": name}, headers=headers), 202)
    if not confirm:
        return key, None
    return key, expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()


def asked_for_key(client, **extra):
    """Password right, key not shown yet: the 202 with the options for the key."""
    asked = log_in(client, **extra)
    assert asked.status_code == 202, asked.text
    return asked.json()


def log_in_with_key(client, key, **extra):
    options = asked_for_key(client)["webauthn"]
    return log_in(client, assertion=key.get(options), **extra)


def turn_on_app(client, headers, mailbox, later, proof=None):
    secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    body = {"code": code_now(secret), **({"proof": proof} if proof else {})}
    expect(client.post("/api/2fa/enable", json=body, headers=headers), 202)
    answer = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()
    later(30)
    return secret, answer


def fresh_headers(client):
    """After a login that should have happened: the CSRF headers of the current session."""
    return {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}


# --- adding a key --------------------------------------------------------------------------------------------------------

def test_a_key_counts_only_after_its_emailed_link_and_brings_the_recovery_codes(mike, mailbox):
    client, headers = mike
    key, _ = add_key(client, headers, mailbox, confirm=False)
    assert client.get("/api/2fa").json()["enabled"] is False, "registered, not confirmed"
    assert log_in(client).status_code == 200, "an unconfirmed key asks for nothing at login"
    headers = fresh_headers(client)
    token = token_in(mailbox.last_to(EMAIL))
    answer = expect(client.post("/api/2fa/confirm", json={"token": token}, headers=headers), 200).json()
    assert answer["enabled"] is True and len(answer["recovery_codes"]) == 10
    state = client.get("/api/2fa").json()
    assert state["enabled"] and state["app"] is False and state["recovery_codes_left"] == 10
    assert [each["name"] for each in state["keys"]] == ["Blue key"]
    assert client.post("/api/2fa/confirm", json={"token": token}, headers=headers).status_code == 400, "once"
    assert account_row(client).webauthn_count == 1


def test_a_key_is_named_plainly(mike, mailbox):
    client, headers = mike
    first, _ = add_key(client, headers, mailbox, name="  my   phone  ")
    assert client.get("/api/2fa").json()["keys"][0]["name"] == "my phone"
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    add_key(client, headers, mailbox, name="", proof={"assertion": first.get(options)})
    names = [each["name"] for each in client.get("/api/2fa").json()["keys"]]
    assert names == ["my phone", "Security key"]


def test_a_names_is_cut_to_forty_letters_and_a_blank_one_gets_a_default():
    from terraforma.accounts import securitykeys

    assert securitykeys.clean_name("y" * 200) == "y" * 40
    assert securitykeys.clean_name("   ") == "Security key" and securitykeys.clean_name(None) == "Security key"


def test_registration_needs_the_options_first_and_its_challenge_works_once(mike, mailbox):
    client, headers = mike
    key = SoftKey()
    options = expect(client.post("/api/2fa/webauthn/options", json={}, headers=headers), 200).json()["options"]
    assert options["rp"]["id"] == "game.test" and options["attestation"] == "none"
    credential = key.create(options)
    expect(client.post("/api/2fa/webauthn/register", json={"credential": credential}, headers=headers), 202)
    assert client.post("/api/2fa/webauthn/register", json={"credential": credential}, headers=headers).status_code == 409, "the challenge is spent"
    assert client.post("/api/2fa/webauthn/register", json={"credential": SoftKey().create(options)}, headers=headers).status_code == 409


def test_a_registration_is_refused_for_the_wrong_site_challenge_or_garbage_and_for_a_key_already_registered(mike, mailbox):
    client, headers = mike

    def register(credential_for, **change):
        options = expect(client.post("/api/2fa/webauthn/options", json=proof_body(), headers=headers), 200).json()["options"]
        return client.post("/api/2fa/webauthn/register", json={"credential": credential_for(options, **change)}, headers=headers)

    def proof_body():
        if not client.get("/api/2fa").json()["enabled"]:
            return {}
        options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
        return {"proof": {"assertion": first.get(options)}}

    first = SoftKey()
    assert register(lambda options, **change: SoftKey().create(options, **change), origin="http://evil.test").status_code == 422
    assert register(lambda options, **change: SoftKey().create(options, challenge="AAAA")).status_code == 422
    assert register(lambda options: "{}").status_code == 422
    assert register(lambda options: "not json").status_code == 422
    assert client.get("/api/2fa").json()["enabled"] is False and count_of(client, WebauthnKey) == 0
    add_key(client, headers, mailbox, key=first)
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    again = expect(client.post("/api/2fa/webauthn/options", json={"proof": {"assertion": first.get(options)}}, headers=headers), 200).json()["options"]
    assert [each["id"] for each in again["excludeCredentials"]] == [first.id], "the same key is not offered twice"
    assert client.post("/api/2fa/webauthn/register", json={"credential": first.create(again)}, headers=headers).status_code == 409


def test_a_second_registration_replaces_the_one_waiting_for_its_link(mike, mailbox):
    client, headers = mike
    first, _ = add_key(client, headers, mailbox, name="First", confirm=False)
    first_link = token_in(mailbox.last_to(EMAIL))
    second, _ = add_key(client, headers, mailbox, name="Second", confirm=False)
    assert count_of(client, WebauthnKey) == 1
    assert client.post("/api/2fa/confirm", json={"token": first_link}, headers=headers).status_code == 400, "the newer request replaced it"
    expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200)
    assert [each["name"] for each in client.get("/api/2fa").json()["keys"]] == ["Second"]


def test_a_key_needs_a_logged_in_player_and_the_csrf_token(app_client):
    assert app_client.post("/api/2fa/webauthn/options", json={}).status_code == 401
    assert app_client.post("/api/2fa/challenge").status_code == 401
    assert app_client.get("/api/2fa/browsers").status_code == 401


# --- logging in with a key -------------------------------------------------------------------------------------------------

@pytest.fixture
def keyed(mike, mailbox):
    """A player whose only second factor is a key, logged out: (client, key, recovery codes)."""
    client, headers = mike
    key, answer = add_key(client, headers, mailbox)
    log_out(client, headers)
    return client, key, answer["recovery_codes"]


def test_login_asks_for_the_key_and_the_key_signs_the_player_in(keyed):
    client, key, _ = keyed
    asked = asked_for_key(client)
    assert asked["needs_code"] is True and asked["methods"] == ["key"]
    assert asked["webauthn"]["rpId"] == "game.test" and [each["id"] for each in asked["webauthn"]["allowCredentials"]] == [key.id]
    assert client.get("/api/me").status_code == 401
    answer = expect(log_in(client, assertion=key.get(asked["webauthn"])), 200).json()
    assert answer["username"] == "Mike" and answer["csrf_token"]
    assert client.get("/api/me").status_code == 200
    stored = in_app_db(client, lambda db: db.scalar(select(WebauthnKey.sign_count)))
    assert stored == key.counter and in_app_db(client, lambda db: db.scalar(select(WebauthnKey.last_used_at))) is not None


def test_an_answer_works_once_and_only_for_the_challenge_it_was_made_for(keyed):
    client, key, _ = keyed
    options = asked_for_key(client)["webauthn"]
    answer = key.get(options)
    expect(log_in(client, assertion=answer), 200)
    log_out(client, fresh_headers(client))
    replay_options = asked_for_key(client)["webauthn"]
    assert log_in(client, assertion=answer).status_code == 401, "the same answer again"
    # The failed try used the challenge up: a new one is needed even for a good answer.
    assert log_in(client, assertion=key.get(replay_options)).status_code == 401
    expect(log_in_with_key(client, key), 200)


def test_a_key_answer_for_another_site_or_origin_or_by_another_key_is_refused(keyed, later):
    client, key, _ = keyed
    for change in ({"origin": "http://evil.test"}, {"rp_id": "evil.test"}):
        options = asked_for_key(client)["webauthn"]
        assert log_in(client, assertion=key.get(options, **change)).status_code == 401
    later(1000)  # (the login limits count in windows of fifteen minutes)
    options = asked_for_key(client)["webauthn"]
    stranger = SoftKey()
    stranger.credential_id = key.credential_id  # claims to be the registered key, signs with its own
    assert log_in(client, assertion=stranger.get(options)).status_code == 401
    options = asked_for_key(client)["webauthn"]
    assert log_in(client, assertion=SoftKey().get(options)).status_code == 401, "a key the account does not have"
    later(2000)
    options = asked_for_key(client)["webauthn"]
    assert log_in(client, assertion="not json at all").status_code == 401
    assert log_in(client, assertion='{"id": 7}').status_code == 401
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)


def test_a_counter_that_does_not_go_up_is_a_cloned_key(keyed):
    client, key, _ = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    log_out(client, fresh_headers(client))
    key.counter -= 1
    assert log_in(client, assertion=key.get(asked_for_key(client)["webauthn"], bump=False)).status_code == 401
    key.counter += 5
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"], bump=False)), 200)


def test_a_key_that_keeps_no_counter_works_every_time(mike, mailbox):
    client, headers = mike
    key = SoftKey(counter=0)
    add_key(client, headers, mailbox, key=key)
    log_out(client, headers)
    for _ in range(2):
        expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"], bump=False)), 200)
        log_out(client, fresh_headers(client))


def test_a_challenge_runs_out(keyed, later):
    client, key, _ = keyed
    options = asked_for_key(client)["webauthn"]
    later(second.CHALLENGE_LIFETIME + 1)
    assert log_in(client, assertion=key.get(options)).status_code == 401


def test_a_registration_challenge_cannot_stand_in_for_a_login_challenge(keyed):
    client, key, _ = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    registration = expect(client.post("/api/2fa/webauthn/options", json={"proof": {"code": "x"}}, headers=headers), 401)
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    made = expect(client.post("/api/2fa/webauthn/options", json={"proof": {"assertion": key.get(options)}}, headers=headers), 200).json()["options"]
    expect(client.post("/api/2fa/challenge", headers=headers), 200)
    # An answer signed over the registration's challenge is not the answer to the login challenge.
    assert client.post("/api/2fa/disable", json={"assertion": key.get(made)}, headers=headers).status_code == 401
    assert registration.status_code == 401


def test_a_code_and_a_key_answer_together_are_refused(keyed):
    client, key, codes = keyed
    options = asked_for_key(client)["webauthn"]
    assert log_in(client, code=codes[0], assertion=key.get(options)).status_code == 422
    expect(log_in(client, code=codes[0]), 200)


def test_a_recovery_code_stands_in_for_a_key_once(keyed):
    client, key, codes = keyed
    asked_for_key(client)
    expect(log_in(client, code=codes[0]), 200)
    assert client.get("/api/2fa").json()["recovery_codes_left"] == 9
    log_out(client, fresh_headers(client))
    assert log_in(client, code=codes[0]).status_code == 401, "each recovery code works once"


def test_the_app_secret_being_set_up_is_no_second_factor_for_an_account_that_has_a_key(keyed, mike):
    client, key, _ = keyed
    options = asked_for_key(client)["webauthn"]
    expect(log_in(client, assertion=key.get(options)), 200)
    headers = fresh_headers(client)
    secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    log_out(client, headers)
    asked_for_key(client)
    assert log_in(client, code=code_now(secret)).status_code == 401, "the app does not count until its link is followed"


def test_wrong_tries_are_counted_against_the_account(keyed):
    client, key, _ = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    for _ in range(ratelimit.TWOFA_CODE_BY_ACCOUNT.attempts - 1):  # (the login used one)
        assert client.post("/api/2fa/disable", json={"code": "000000"}, headers=headers).status_code == 401
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    assert client.post("/api/2fa/disable", json={"code": "000000"}, headers=headers).status_code == 429
    assert client.post("/api/2fa/disable", json={"assertion": key.get(options)}, headers=headers).status_code == 429, "a right answer waits too"


# --- the app and a key together ---------------------------------------------------------------------------------------------

def test_adding_the_app_to_an_account_with_a_key_needs_the_key_and_keeps_the_recovery_codes(keyed, mailbox, later):
    client, key, codes = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    assert client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers).status_code == 401, "no proof of the key"
    assert client.post("/api/2fa/enable", json={"code": code_now(secret), "proof": {"code": "000000"}}, headers=headers).status_code == 401
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    body = {"code": code_now(secret), "proof": {"assertion": key.get(options)}}
    expect(client.post("/api/2fa/enable", json=body, headers=headers), 202)
    answer = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()
    assert answer == {"enabled": True, "recovery_codes": None}, "the codes the account has still stand"
    state = client.get("/api/2fa").json()
    assert state["app"] is True and len(state["keys"]) == 1 and state["recovery_codes_left"] == 10
    assert twofa.methods(account_row(client)) == ["app", "key"]


def test_a_recovery_code_does_not_pass_for_the_code_of_the_app_being_set_up(keyed):
    client, key, codes = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    expect(client.post("/api/2fa/setup", headers=headers), 200)
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    body = {"code": codes[0], "proof": {"assertion": key.get(options)}}
    assert client.post("/api/2fa/enable", json=body, headers=headers).status_code == 401


def test_adding_a_key_when_the_app_is_on_needs_a_current_code(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    assert client.post("/api/2fa/webauthn/options", json={}, headers=headers).status_code == 401
    assert client.post("/api/2fa/webauthn/options", json={"proof": {"code": "000000"}}, headers=headers).status_code == 401
    key, answer = add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    assert answer["recovery_codes"] is None
    assert twofa.methods(account_row(client)) == ["app", "key"]


def test_a_player_with_both_logs_in_with_either(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    later(60)
    key, _ = add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    log_out(client, headers)
    asked = asked_for_key(client)
    assert asked["methods"] == ["app", "key"]
    expect(log_in(client, assertion=key.get(asked["webauthn"])), 200)
    log_out(client, fresh_headers(client))
    later(120)
    asked_for_key(client)
    expect(log_in(client, code=code_now(secret)), 200)


# --- removing and turning off --------------------------------------------------------------------------------------------------

def test_the_last_method_cannot_be_removed_only_turned_off(keyed, mailbox):
    client, key, _ = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    key_id = client.get("/api/2fa").json()["keys"][0]["id"]
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    gone = client.post("/api/2fa/webauthn/remove", json={"key_id": key_id, "assertion": key.get(options)}, headers=headers)
    assert gone.status_code == 409
    assert client.post("/api/2fa/remove-app", json={"code": "x"}, headers=headers).status_code == 409


def test_removing_one_of_two_keys_needs_proof_and_the_link(mike, mailbox):
    client, headers = mike
    first, _ = add_key(client, headers, mailbox, name="First")
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    second_key, _ = add_key(client, headers, mailbox, name="Second", proof={"assertion": first.get(options)})
    keys = client.get("/api/2fa").json()["keys"]
    first_id = next(each["id"] for each in keys if each["name"] == "First")
    assert client.post("/api/2fa/webauthn/remove", json={"key_id": first_id}, headers=headers).status_code == 401
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    expect(client.post("/api/2fa/webauthn/remove", json={"key_id": first_id, "assertion": second_key.get(options)}, headers=headers), 202)
    assert len(client.get("/api/2fa").json()["keys"]) == 2, "not until the link is followed"
    answer = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()
    assert answer == {"enabled": True, "recovery_codes": None}
    assert [each["name"] for each in client.get("/api/2fa").json()["keys"]] == ["Second"]
    assert account_row(client).webauthn_count == 1
    assert client.post("/api/2fa/webauthn/remove", json={"key_id": 99999, "code": "x"}, headers=headers).status_code == 404


def test_a_key_of_another_account_cannot_be_removed(mike, mailbox):
    client, headers = mike
    add_key(client, headers, mailbox)
    key_id = in_app_db(client, lambda db: db.scalar(select(WebauthnKey.id)))
    log_out(client, headers)
    in_app_db(client, lambda db: create_account(db, "Kim", PASSWORD, email="kim@example.com", confirmed=True))
    kim = client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).json()
    assert client.post("/api/2fa/webauthn/remove", json={"key_id": key_id, "code": "x"}, headers={"X-CSRF-Token": kim["csrf_token"]}).status_code == 404


def test_the_app_can_go_while_a_key_stays(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    later(60)
    key, _ = add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    later(120)
    expect(client.post("/api/2fa/remove-app", json={"code": code_now(secret)}, headers=headers), 202)
    expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200)
    state = client.get("/api/2fa").json()
    assert state["app"] is False and state["enabled"] and len(state["keys"]) == 1 and state["recovery_codes_left"] == 10
    log_out(client, headers)
    assert asked_for_key(client)["methods"] == ["key"]


def test_the_app_can_be_set_up_again_after_it_was_removed(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    later(60)
    key, _ = add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    later(120)
    expect(client.post("/api/2fa/remove-app", json={"code": code_now(secret)}, headers=headers), 202)
    expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200)
    later(3700)  # (changes to two-factor login are limited per hour)
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    new_secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    assert new_secret != secret
    body = {"code": code_now(new_secret), "proof": {"assertion": key.get(options)}}
    expect(client.post("/api/2fa/enable", json=body, headers=headers), 202)


def test_turning_it_off_takes_every_method_and_the_codes(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    later(60)
    add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    later(120)
    expect(client.post("/api/2fa/disable", json={"code": code_now(secret)}, headers=headers), 202)
    assert client.get("/api/2fa").json()["enabled"] is True, "not until the link is followed"
    answer = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()
    assert answer == {"enabled": False}
    assert client.get("/api/2fa").json() == {"enabled": False, "recovery_codes_left": 0, "app": False, "keys": []}
    assert count_of(client, WebauthnKey) == 0
    row = account_row(client)
    assert row.webauthn_count == 0 and row.totp_secret is None and row.recovery_codes is None
    log_out(client, headers)
    expect(log_in(client), 200)


def test_new_recovery_codes_for_an_account_with_only_a_key(keyed, mailbox):
    client, key, old = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    expect(client.post("/api/2fa/recovery-codes", json={"assertion": key.get(options)}, headers=headers), 202)
    new = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200).json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    log_out(client, headers)
    asked_for_key(client)
    assert log_in(client, code=old[0]).status_code == 401
    expect(log_in(client, code=new[0]), 200)


def test_changing_the_email_address_takes_a_key_when_that_is_the_second_factor(keyed, mailbox):
    client, key, _ = keyed
    expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200)
    headers = fresh_headers(client)
    assert client.post("/api/email-change", json={"email": "new@example.com"}, headers=headers).status_code == 401
    options = expect(client.post("/api/2fa/challenge", headers=headers), 200).json()["webauthn"]
    expect(client.post("/api/email-change", json={"email": "new@example.com", "assertion": key.get(options)}, headers=headers), 202)
    assert mailbox.last_to("new@example.com")


def test_the_challenge_call_names_the_methods_and_offers_a_key_only_when_there_is_one(mike, mailbox, later):
    client, headers = mike
    assert expect(client.post("/api/2fa/challenge", headers=headers), 200).json() == {"methods": [], "webauthn": None, "remember_days": 30}
    turn_on_app(client, headers, mailbox, later)
    assert expect(client.post("/api/2fa/challenge", headers=headers), 200).json() == {"methods": ["app"], "webauthn": None, "remember_days": 30}


# --- remembered browsers ---------------------------------------------------------------------------------------------------

@pytest.fixture
def protected(mike, mailbox, later):
    """An account with the app on, logged out: (client, the app's secret)."""
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    log_out(client, headers)
    later(60)
    return client, secret


def test_a_browser_that_asked_to_be_remembered_skips_the_second_factor(protected, later):
    client, secret = protected
    asked_for_key(client)
    answer = expect(log_in(client, code=code_now(secret), remember=True), 200)
    assert "tf_remember_" in answer.headers["set-cookie"] and "HttpOnly" in answer.headers["set-cookie"]
    log_out(client, {"X-CSRF-Token": answer.json()["csrf_token"]})
    later(3600)
    expect(log_in(client), 200)
    assert client.get("/api/me").status_code == 200


def test_a_browser_that_did_not_ask_is_asked_again(protected, later):
    client, secret = protected
    expect(log_in(client, code=code_now(secret)), 200)
    assert not [name for name in client.cookies.keys() if name.startswith("tf_remember_")]
    log_out(client, fresh_headers(client))
    assert log_in(client).status_code == 202


def test_a_remembered_browser_is_remembered_for_the_days_the_game_sets_and_no_longer(protected, later):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    log_out(client, fresh_headers(client))
    later(60 + 29 * 86400)
    expect(log_in(client), 200)
    log_out(client, fresh_headers(client))
    later(60 + 31 * 86400)
    assert log_in(client).status_code == 202, "the thirty days are over"


class ShortMemory(SignIn):
    remember_browser_days = 2

    def single_method_note(self, account, methods):
        return f"Add another: you have {' and '.join(methods)}."


class NoMemory(SignIn):
    remember_browser_days = 0


@pytest.mark.parametrize("game", [Game(name="Short", sign_in=ShortMemory())])
def test_a_game_can_shorten_the_memory_and_word_the_note(mike, mailbox, later, game):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    log_out(client, headers)
    later(60)
    asked_for_key(client)
    answer = expect(log_in(client, code=code_now(secret), remember=True), 200)
    assert answer.json()["note"] == "Add another: you have app."
    log_out(client, {"X-CSRF-Token": answer.json()["csrf_token"]})
    later(60 + 86400)
    expect(log_in(client), 200)
    log_out(client, fresh_headers(client))
    later(60 + 3 * 86400)
    assert log_in(client).status_code == 202


@pytest.mark.parametrize("game", [Game(name="Never", sign_in=NoMemory())])
def test_a_game_can_turn_remembering_off(mike, mailbox, later, game):
    client, headers = mike
    secret, _ = turn_on_app(client, headers, mailbox, later)
    log_out(client, headers)
    later(60)
    asked_for_key(client)
    answer = expect(log_in(client, code=code_now(secret), remember=True), 200)
    assert "set-cookie" not in answer.headers or "tf_remember_" not in answer.headers["set-cookie"]
    assert count_of(client, RememberedBrowser) == 0


def test_a_browser_remembered_for_one_account_does_not_help_another(protected, mailbox, later):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    log_out(client, fresh_headers(client))
    in_app_db(client, lambda db: create_account(db, "Kim", PASSWORD, email="kim@example.com", confirmed=True))
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).json()["csrf_token"]}
    kim_secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    expect(client.post("/api/2fa/enable", json={"code": code_now(kim_secret)}, headers=headers), 202)
    expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to("kim@example.com"))}, headers=headers), 200)
    log_out(client, headers)
    assert client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).status_code == 202
    expect(log_in(client), 200)


def test_a_made_up_or_altered_cookie_does_nothing(protected):
    client, secret = protected
    client.cookies.set("tf_remember_1", "made-up-by-someone")
    assert log_in(client).status_code == 202
    client.cookies.clear()
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    log_out(client, fresh_headers(client))
    real = next(cookie for cookie in client.cookies.jar if cookie.name == "tf_remember_1")
    client.cookies.clear()
    client.cookies.set("tf_remember_1", real.value + "x", domain=real.domain, path=real.path)
    assert log_in(client).status_code == 202


def test_remembered_browsers_are_listed_and_forgotten(protected, later):
    client, secret = protected
    answer = expect(log_in(client, code=code_now(secret), remember=True), 200)
    headers = {"X-CSRF-Token": answer.json()["csrf_token"]}
    listed = expect(client.get("/api/2fa/browsers"), 200).json()["browsers"]
    assert len(listed) == 1 and listed[0]["this"] is True and listed[0]["label"]
    expect(client.post("/api/2fa/browsers/forget", json={"id": listed[0]["id"] + 100}, headers=headers), 200)
    assert len(client.get("/api/2fa/browsers").json()["browsers"]) == 1, "another id forgets nothing"
    assert expect(client.post("/api/2fa/browsers/forget", json={}, headers=headers), 200).json() == {"forgotten": 1}
    assert client.get("/api/2fa/browsers").json()["browsers"] == []
    log_out(client, headers)
    assert log_in(client).status_code == 202


def test_forgetting_needs_the_csrf_token(protected):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    assert client.post("/api/2fa/browsers/forget", json={}).status_code == 403


def test_a_password_reset_ends_every_remembered_browser(protected, mailbox, later):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    log_out(client, fresh_headers(client))
    expect(client.post("/api/password-reset", json={"email": EMAIL}), 202)
    token = token_in(mailbox.last_to(EMAIL))
    expect(client.post("/api/password-reset/complete", json={"token": token, "password": "another long password"}), 200)
    answer = client.post("/api/login", json={"username": "Mike", "password": "another long password"})
    assert answer.status_code == 202, "the reset logged every remembered browser out too"
    assert client.get("/api/2fa/browsers").status_code == 401


def test_changing_two_factor_login_ends_every_remembered_browser(protected, mailbox, later):
    client, secret = protected
    answer = expect(log_in(client, code=code_now(secret), remember=True), 200)
    headers = {"X-CSRF-Token": answer.json()["csrf_token"]}
    assert len(client.get("/api/2fa/browsers").json()["browsers"]) == 1
    later(120)
    add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    assert client.get("/api/2fa/browsers").json()["browsers"] == []
    assert count_of(client, RememberedBrowser) == 0


def test_a_remembered_browser_row_keeps_only_a_hash(protected):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    cookie = next(value for name, value in client.cookies.items() if name.startswith("tf_remember_"))
    row = in_app_db(client, lambda db: db.scalar(select(RememberedBrowser.secret_hash)))
    assert cookie not in row and len(row) == 64


def test_one_account_keeps_at_most_ten_remembered_browsers(protected, later):
    client, secret = protected
    for number in range(remember.MAX_BROWSERS + 3):
        later(60 + number * 1000)
        client.cookies.clear()
        asked_for_key(client)
        expect(log_in(client, code=code_now(secret), remember=True), 200)
        log_out(client, fresh_headers(client))
    assert count_of(client, RememberedBrowser) == remember.MAX_BROWSERS


# --- the note for a single method -----------------------------------------------------------------------------------------

def test_a_player_with_one_second_factor_gets_the_note_and_one_with_two_does_not(mike, mailbox, later):
    client, headers = mike
    assert expect(log_in(client), 200).json()["note"] is None, "no second factor at all: no note"
    headers = fresh_headers(client)
    secret, _ = turn_on_app(client, headers, mailbox, later)
    log_out(client, headers)
    later(60)
    asked_for_key(client)
    one = expect(log_in(client, code=code_now(secret)), 200).json()
    assert "only one" in one["note"]
    headers = {"X-CSRF-Token": one["csrf_token"]}
    later(120)
    key, _ = add_key(client, headers, mailbox, proof={"code": code_now(secret)})
    log_out(client, headers)
    both = expect(log_in(client, assertion=key.get(asked_for_key(client)["webauthn"])), 200).json()
    assert both["note"] is None


# --- the admin's reset -------------------------------------------------------------------------------------------------------

def make_admin(client):
    from terraforma.challenge.service import set_admin

    in_app_db(client, lambda db: create_account(db, "Boss", PASSWORD, email="boss@example.com", confirmed=True))
    boss = account_row(client, "Boss")
    in_app_db(client, lambda db: set_admin(db, boss.id))
    answer = client.post("/api/login", json={"username": "Boss", "password": PASSWORD})
    return {"X-CSRF-Token": answer.json()["csrf_token"]}


def test_an_admin_resets_a_players_two_factor_login_and_the_player_is_told(keyed, mailbox):
    client, key, _ = keyed
    admin_headers = make_admin(client)
    expect(client.post("/api/admin/2fa-reset", json={"username": "mike"}, headers=admin_headers), 200)
    row = account_row(client)
    assert row.webauthn_count == 0 and row.recovery_codes is None and row.session_version == 1
    assert count_of(client, WebauthnKey) == 0
    assert "two-factor login was reset" in mailbox.last_to(EMAIL).subject
    log_out(client, admin_headers)
    expect(log_in(client), 200)


def test_the_reset_logs_the_player_out_and_ends_remembered_browsers(protected, mailbox):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    players_cookies = list(client.cookies.jar)
    client.cookies.clear()
    admin_headers = make_admin(client)  # the admin, in another browser
    expect(client.post("/api/admin/2fa-reset", json={"username": "Mike"}, headers=admin_headers), 200)
    client.cookies.clear()
    for cookie in players_cookies:
        client.cookies.set(cookie.name, cookie.value, domain=cookie.domain, path=cookie.path)
    assert client.get("/api/me").status_code == 401, "logged out everywhere"
    assert count_of(client, RememberedBrowser) == 0


def test_only_an_admin_may_reset_and_an_unknown_player_is_not_found(mike):
    client, headers = mike
    in_app_db(client, lambda db: create_account(db, "Kim", PASSWORD, email="kim@example.com", confirmed=True))
    kim = {"X-CSRF-Token": client.post("/api/login", json={"username": "Kim", "password": PASSWORD}).json()["csrf_token"]}
    assert client.post("/api/admin/2fa-reset", json={"username": "Mike"}, headers=kim).status_code == 403
    assert client.post("/api/admin/2fa-reset", json={"username": "Mike"}).status_code == 403
    log_out(client, kim)
    assert client.post("/api/admin/2fa-reset", json={"username": "Mike"}).status_code == 401
    admin = make_admin(client)
    assert client.post("/api/admin/2fa-reset", json={"username": "nobody"}, headers=admin).status_code == 404


def test_the_resets_are_rate_limited(mike):
    client, headers = mike
    log_out(client, headers)
    admin = make_admin(client)
    for _ in range(ratelimit.ADMIN_RESET_BY_ACCOUNT.attempts):
        client.post("/api/admin/2fa-reset", json={"username": "nobody"}, headers=admin)
    assert client.post("/api/admin/2fa-reset", json={"username": "nobody"}, headers=admin).status_code == 429


# --- tidying up --------------------------------------------------------------------------------------------------------------

def test_housekeeping_removes_expired_browsers_and_keys_nobody_confirmed(protected, mike, later):
    client, secret = protected
    expect(log_in(client, code=code_now(secret), remember=True), 200)
    headers = fresh_headers(client)
    later(120)
    options = expect(client.post("/api/2fa/webauthn/options", json={"proof": {"code": code_now(secret)}}, headers=headers), 200).json()["options"]
    expect(client.post("/api/2fa/webauthn/register", json={"credential": SoftKey().create(options)}, headers=headers), 202)

    async def sweep(db):
        names = {}
        for name in ("expired_remembered_browsers", "unconfirmed_security_keys"):
            names[name] = await housekeeping.JOBS[name](db, wallclock.timestamp(), client.app.state.settings)
        return names

    assert in_app_db(client, sweep) == {"expired_remembered_browsers": 0, "unconfirmed_security_keys": 0}
    later(60 + 31 * 86400)
    assert in_app_db(client, sweep) == {"expired_remembered_browsers": 1, "unconfirmed_security_keys": 1}
    assert count_of(client, RememberedBrowser) == 0 and count_of(client, WebauthnKey) == 0


def test_the_new_game_choices_have_sensible_defaults():
    hook = SignIn()
    assert hook.remember_browser_days == 30
    assert hook.single_method_note(None, ["app", "key"]) is None and hook.single_method_note(None, []) is None
    assert "only one" in hook.single_method_note(None, ["key"])
