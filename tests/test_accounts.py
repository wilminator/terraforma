"""Getting in: register, confirm, log in and out, reset a password, rate limits, on every database."""

import re

import pytest

from terraforma import wallclock
from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account

from .conftest import in_app_db

MIKE = {"username": "Mike", "email": "mike@example.com", "password": "correct horse battery"}


def token_in(mail) -> str:
    return re.search(r"token=([A-Za-z0-9_.-]+)", mail.body).group(1)


def register(client, **changes) -> int:
    return client.post("/api/register", json={**MIKE, **changes}).status_code


def log_in(client, password=MIKE["password"]):
    return client.post("/api/login", json={"username": "mike", "password": password})


@pytest.fixture
def later(monkeypatch):
    """Moves the wall clock forward from the real now: later(seconds)."""
    real_now = wallclock.now

    def move(seconds: int) -> None:
        from datetime import timedelta

        moved = real_now() + timedelta(seconds=seconds)
        monkeypatch.setattr(wallclock, "now", lambda: moved)

    return move


# --- registering and confirming --------------------------------------------

def test_registering_sends_a_confirmation_link_and_login_waits_for_it(app_client, mailbox):
    assert register(app_client) == 202
    mail = mailbox.last_to("mike@example.com")
    assert "confirm" in mail.subject
    assert "http://game.test/confirm-email?token=" in mail.body
    assert log_in(app_client).status_code == 403, "not until the address is confirmed"
    answer = app_client.post("/api/confirm-email", json={"token": token_in(mail)})
    assert answer.json() == {"confirmed": True, "username": "Mike"}
    assert log_in(app_client).status_code == 200
    assert app_client.get("/api/me").json() == {"username": "Mike"}


def test_an_unconfirmed_login_says_so_only_with_the_right_password(app_client):
    register(app_client)
    assert log_in(app_client, "wrong horse battery").status_code == 401, "no hint for a wrong password"
    assert log_in(app_client).status_code == 403


def test_confirmation_links_are_signed_and_expire(app_client, mailbox, later):
    register(app_client)
    token = token_in(mailbox.last_to("mike@example.com"))
    assert app_client.post("/api/confirm-email", json={"token": token[:-2] + "xx"}).status_code == 400
    later(3 * 24 * 60 * 60 + 1)
    assert app_client.post("/api/confirm-email", json={"token": token}).status_code == 400


def test_a_reset_link_is_not_a_confirmation_link(app_client, mailbox):
    in_app_db(app_client, lambda db: create_account(db, "Mike", MIKE["password"], email=MIKE["email"], confirmed=True))
    app_client.post("/api/password-reset", json={"email": MIKE["email"]})
    reset = token_in(mailbox.last_to(MIKE["email"]))
    assert app_client.post("/api/confirm-email", json={"token": reset}).status_code == 400


def test_registering_an_address_that_has_an_account_looks_the_same_but_warns_its_owner(app_client, mailbox):
    assert register(app_client) == 202
    assert register(app_client, username="Someone", email="MIKE@example.com") == 202, "same answer as success"
    assert "already has one" in mailbox.last_to("MIKE@example.com").body
    assert len([m for m in mailbox.sent if "confirm your email" in m.subject]) == 1


def test_a_taken_username_is_said_plainly(app_client):
    register(app_client)
    assert register(app_client, username="MIKE", email="other@example.com") == 409


@pytest.mark.parametrize(
    "change",
    [{"username": "a"}, {"email": "nope"}, {"password": "short"}, {"password": 12345678901234}, {"extra": 1}],
    ids=["short-name", "bad-email", "short-password", "number-password", "extra-field"],
)
def test_registration_rules(app_client, mailbox, change):
    assert register(app_client, **change) == 422
    assert mailbox.sent == []


# --- logging out, and resetting the password --------------------------------

@pytest.fixture
def mike(app_client):
    in_app_db(app_client, lambda db: create_account(db, "Mike", MIKE["password"], email=MIKE["email"], confirmed=True))
    return app_client


def test_logout_needs_the_token_and_ends_the_login(mike):
    token = log_in(mike).json()["csrf_token"]
    assert mike.post("/api/logout").status_code == 403
    assert mike.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert mike.get("/api/me").status_code == 401


def test_a_password_reset_works_once_and_logs_out_everywhere(mike, mailbox):
    log_in(mike)
    assert mike.get("/api/me").status_code == 200
    assert mike.post("/api/password-reset", json={"email": "MIKE@example.com"}).status_code == 202
    mail = mailbox.last_to(MIKE["email"])
    assert "http://game.test/reset-password?token=" in mail.body
    token = token_in(mail)
    answer = mike.post("/api/password-reset/complete", json={"token": token, "password": "a brand new password"})
    assert answer.status_code == 200
    assert mike.get("/api/me").status_code == 401, "logged out"
    assert log_in(mike).status_code == 401, "the old password is gone"
    assert log_in(mike, "a brand new password").status_code == 200
    again = mike.post("/api/password-reset/complete", json={"token": token, "password": "yet another password"})
    assert again.status_code == 400, "the link works once"


def test_a_reset_ends_logins_in_other_browsers_too(mike, mailbox):
    from starlette.testclient import TestClient

    other = TestClient(mike.app)
    other.portal = mike.portal  # same running app
    log_in(other)
    assert other.get("/api/me").status_code == 200
    mike.post("/api/password-reset", json={"email": MIKE["email"]})
    mike.post("/api/password-reset/complete", json={"token": token_in(mailbox.last_to(MIKE["email"])), "password": "a brand new password"})
    assert other.get("/api/me").status_code == 401


def test_a_reset_for_an_unknown_address_looks_the_same_and_sends_nothing(mike, mailbox):
    assert mike.post("/api/password-reset", json={"email": "nobody@example.com"}).status_code == 202
    assert mailbox.sent == []


def test_reset_links_expire_after_an_hour(mike, mailbox, later):
    mike.post("/api/password-reset", json={"email": MIKE["email"]})
    token = token_in(mailbox.last_to(MIKE["email"]))
    later(60 * 60 + 1)
    answer = mike.post("/api/password-reset/complete", json={"token": token, "password": "a brand new password"})
    assert answer.status_code == 400


def test_a_new_password_follows_the_rules(mike, mailbox):
    mike.post("/api/password-reset", json={"email": MIKE["email"]})
    token = token_in(mailbox.last_to(MIKE["email"]))
    assert mike.post("/api/password-reset/complete", json={"token": token, "password": "short"}).status_code == 422
    assert mike.post("/api/password-reset/complete", json={"token": token, "password": "long enough now"}).status_code == 200


# --- rate limits --------------------------------------------------------------

def test_too_many_logins_for_one_name_are_refused_until_the_window_ends(mike, later):
    for _ in range(ratelimit.LOGIN_BY_NAME.attempts):
        assert log_in(mike, "wrong horse battery").status_code == 401
    refused = log_in(mike)
    assert refused.status_code == 429, "even with the right password"
    assert 0 < int(refused.headers["Retry-After"]) <= ratelimit.LOGIN_BY_NAME.window
    later(ratelimit.LOGIN_BY_NAME.window)
    assert log_in(mike).status_code == 200


def test_reset_requests_are_limited_per_address(mike, mailbox):
    for _ in range(ratelimit.RESET_BY_EMAIL.attempts):
        assert mike.post("/api/password-reset", json={"email": MIKE["email"]}).status_code == 202
    assert mike.post("/api/password-reset", json={"email": "Mike@Example.com"}).status_code == 429
    assert len(mailbox.sent) == ratelimit.RESET_BY_EMAIL.attempts


def test_registrations_are_limited_per_caller(app_client):
    for n in range(ratelimit.REGISTER_BY_ADDRESS.attempts):
        assert register(app_client, username=f"player{n}", email=f"p{n}@example.com") == 202
    assert register(app_client, username="onemore", email="more@example.com") == 429


def test_rate_limits_store_no_names_or_addresses(mike):
    log_in(mike, "wrong horse battery")
    from sqlalchemy import select

    from terraforma.models import RateLimitHit

    subjects = in_app_db(mike, lambda db: _subjects(db, select(RateLimitHit.subject)))
    assert subjects and all(re.fullmatch(r"[0-9a-f]{64}", s) for s in subjects)


async def _subjects(db, query):
    return list((await db.scalars(query)).all())
