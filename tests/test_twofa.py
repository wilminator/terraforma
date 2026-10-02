"""Two-factor login: setup, codes that work once, recovery codes, and changes confirmed by emailed link."""

import re

import pytest

from terraforma.accounts import twofa
from terraforma.accounts.service import create_account
from terraforma.keys import KeyRing
from terraforma.testing import in_app_db

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


def turn_on(client, headers, mailbox, later):
    """Sets 2FA up and confirms it. Returns (secret, recovery codes)."""
    secret = expect(client.post("/api/2fa/setup", headers=headers), 200).json()["secret"]
    expect(client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers), 202)
    answer = expect(client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers), 200)
    later(30)  # the code used to enable is spent; the next login needs a fresh step
    return secret, answer.json()["recovery_codes"]


def log_in(client, **extra):
    return client.post("/api/login", json={"username": "Mike", "password": PASSWORD, **extra})


# --- the codes themselves ------------------------------------------------------

def test_the_code_matches_the_rfc_6238_test_vector():
    # RFC 6238 appendix B, SHA-1, T = 59 s: 94287082 (8 digits), whose last six are 287082.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890" in base32
    assert twofa.code_at(secret, 59 // 30) == "287082"


def test_a_code_works_once_and_a_neighbouring_step_is_forgiven():
    secret = twofa.new_secret()
    now = twofa.current_step()
    assert twofa.matching_step(secret, twofa.code_at(secret, now - 1), None) == now - 1, "a slow clock"
    assert twofa.matching_step(secret, twofa.code_at(secret, now), now) is None, "already used"
    assert twofa.matching_step(secret, twofa.code_at(secret, now + 5), None) is None, "too far off"
    assert twofa.matching_step(secret, "12345", None) is None
    assert twofa.matching_step(secret, "١٢٣٤٥٦", None) is None, "only ASCII digits"


# --- turning it on ---------------------------------------------------------------

def test_setup_does_nothing_until_a_code_and_the_emailed_link_confirm_it(mike, mailbox):
    client, headers = mike
    setup = expect(client.post("/api/2fa/setup", headers=headers), 200).json()
    assert setup["uri"].startswith("otpauth://totp/") and setup["secret"] in setup["uri"]
    assert client.get("/api/2fa").json() == {"enabled": False, "recovery_codes_left": 0}
    assert client.post("/api/2fa/enable", json={"code": "000000"}, headers=headers).status_code == 401
    assert client.post("/api/2fa/enable", json={"code": code_now(setup["secret"])}, headers=headers).status_code == 202
    assert client.get("/api/2fa").json()["enabled"] is False, "not until the link is followed"
    token = token_in(mailbox.last_to(EMAIL))
    answer = client.post("/api/2fa/confirm", json={"token": token}, headers=headers).json()
    assert answer["enabled"] is True and len(answer["recovery_codes"]) == 10
    assert client.get("/api/2fa").json() == {"enabled": True, "recovery_codes_left": 10}
    assert client.post("/api/2fa/confirm", json={"token": token}, headers=headers).status_code == 400, "once"


def test_the_secret_is_stored_encrypted(mike):
    client, headers = mike
    secret = client.post("/api/2fa/setup", headers=headers).json()["secret"]
    from sqlalchemy import text

    async def stored(db):
        return (await db.execute(text("select totp_secret from accounts"))).scalar_one()

    value = in_app_db(client, stored)
    assert secret not in value and KeyRing.load(client.app.state.settings.key_dir).decrypt(value, twofa.PURPOSE) == secret


def test_the_2fa_calls_need_login_and_the_csrf_token(mike):
    client, headers = mike
    assert client.post("/api/2fa/setup").status_code == 403
    client.post("/api/logout", headers=headers)
    assert client.post("/api/2fa/setup").status_code == 401
    assert client.get("/api/2fa").status_code == 401


def test_a_confirmation_link_only_works_for_its_own_account_and_the_latest_request(mike, mailbox, later):
    client, headers = mike
    secret = client.post("/api/2fa/setup", headers=headers).json()["secret"]
    client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers)
    first = token_in(mailbox.last_to(EMAIL))
    later(30)
    client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers)
    second = token_in(mailbox.last_to(EMAIL))
    assert client.post("/api/2fa/confirm", json={"token": first}, headers=headers).status_code == 400, "replaced"
    in_app_db(client, lambda db: create_account(db, "Ann", PASSWORD, email="ann@example.com", confirmed=True))
    ann = client.post("/api/login", json={"username": "Ann", "password": PASSWORD}).json()["csrf_token"]
    assert client.post("/api/2fa/confirm", json={"token": second}, headers={"X-CSRF-Token": ann}).status_code == 400
    mike_again = client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    assert client.post("/api/2fa/confirm", json={"token": second}, headers={"X-CSRF-Token": mike_again}).status_code == 200


def test_the_confirmation_link_expires(mike, mailbox, later):
    client, headers = mike
    secret = client.post("/api/2fa/setup", headers=headers).json()["secret"]
    client.post("/api/2fa/enable", json={"code": code_now(secret)}, headers=headers)
    token = token_in(mailbox.last_to(EMAIL))
    later(60 * 60 + 1)
    assert client.post("/api/2fa/confirm", json={"token": token}, headers=headers).status_code == 400


# --- logging in ---------------------------------------------------------------------

def test_login_with_2fa_asks_for_a_code_and_each_code_works_once(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on(client, headers, mailbox, later)
    client.post("/api/logout", headers=headers)
    asked = log_in(client)
    assert asked.status_code == 202 and asked.json() == {"needs_code": True}
    assert client.get("/api/me").status_code == 401, "no login without the code"
    assert log_in(client, code="000000").status_code == 401
    code = code_now(secret)
    assert log_in(client, code=code).status_code == 200
    assert log_in(client, code=code).status_code == 401, "the same code twice"
    later(60)
    assert log_in(client, code=code_now(secret)).status_code == 200


def test_a_wrong_password_never_reaches_the_code_step(mike, mailbox, later):
    client, headers = mike
    turn_on(client, headers, mailbox, later)
    wrong = client.post("/api/login", json={"username": "Mike", "password": "wrong horse battery"})
    assert wrong.status_code == 401 and "needs_code" not in wrong.text


def test_recovery_codes_work_once_in_place_of_a_code(mike, mailbox, later):
    client, headers = mike
    _, recovery = turn_on(client, headers, mailbox, later)
    client.post("/api/logout", headers=headers)
    assert log_in(client, code=recovery[0].upper()).status_code == 200, "case doesn't matter"
    assert log_in(client, code=recovery[0]).status_code == 401, "used up"
    assert log_in(client, code=recovery[1].replace("-", " ")).status_code == 200
    token = client.post("/api/login", json={"username": "Mike", "password": PASSWORD, "code": recovery[2]}).json()["csrf_token"]
    assert client.get("/api/2fa").json()["recovery_codes_left"] == 7
    assert token


def test_guessing_codes_is_rate_limited(mike, mailbox, later):
    client, headers = mike
    turn_on(client, headers, mailbox, later)
    client.post("/api/logout", headers=headers)
    answers = [log_in(client, code=f"{n:06d}") for n in range(12)]
    # Ten guesses are allowed in the window; turning 2FA on used one.
    assert [a.status_code for a in answers] == [401] * 9 + [429] * 3, [(a.status_code, a.text) for a in answers]


# --- turning it off and new recovery codes ---------------------------------------

def test_turning_it_off_needs_a_code_and_the_link(mike, mailbox, later):
    client, headers = mike
    secret, _ = turn_on(client, headers, mailbox, later)
    assert client.post("/api/2fa/disable", json={"code": "000000"}, headers=headers).status_code == 401
    assert client.post("/api/2fa/disable", json={"code": code_now(secret)}, headers=headers).status_code == 202
    assert client.get("/api/2fa").json()["enabled"] is True, "not until the link is followed"
    answer = client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers)
    assert answer.json() == {"enabled": False}
    client.post("/api/logout", headers=headers)
    assert log_in(client).status_code == 200, "no code needed any more"


def test_turning_off_a_recovery_code_will_do_for_a_lost_phone(mike, mailbox, later):
    client, headers = mike
    _, recovery = turn_on(client, headers, mailbox, later)
    assert client.post("/api/2fa/disable", json={"code": recovery[0]}, headers=headers).status_code == 202


def test_new_recovery_codes_replace_the_old_ones(mike, mailbox, later):
    client, headers = mike
    secret, old = turn_on(client, headers, mailbox, later)
    assert client.post("/api/2fa/recovery-codes", json={"code": code_now(secret)}, headers=headers).status_code == 202
    new = client.post("/api/2fa/confirm", json={"token": token_in(mailbox.last_to(EMAIL))}, headers=headers).json()["recovery_codes"]
    assert set(new).isdisjoint(old) and len(new) == 10
    client.post("/api/logout", headers=headers)
    assert log_in(client, code=old[0]).status_code == 401
    assert log_in(client, code=new[0]).status_code == 200


def test_off_means_off(mike):
    client, headers = mike
    assert client.post("/api/2fa/disable", json={"code": "000000"}, headers=headers).status_code == 409
    assert client.post("/api/2fa/recovery-codes", json={"code": "000000"}, headers=headers).status_code == 409
    assert client.post("/api/2fa/enable", json={"code": "000000"}, headers=headers).status_code == 409, "set it up first"


def test_setup_is_refused_while_2fa_is_on(mike, mailbox, later):
    client, headers = mike
    turn_on(client, headers, mailbox, later)
    assert client.post("/api/2fa/setup", headers=headers).status_code == 409


# --- rotating keys --------------------------------------------------------------------

def test_the_secret_is_found_by_key_rotation(mike):
    from terraforma.keys import ENCRYPTED

    assert any(entry.column == "totp_secret" and entry.purpose == twofa.PURPOSE for entry in ENCRYPTED)
