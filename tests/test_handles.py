"""The public handle: never the username or email, unique, set only by its logged-in owner."""

import pytest

from terraforma.accounts.service import AccountError, check_handle, create_account
from terraforma.testing import in_app_db

PASSWORD = "correct horse battery"


@pytest.mark.parametrize("handle", ["Mike", "MIKE", "mike", "M.i-k_e", "mike.wilmes.example.com", "Mike.Wilmes", "mike wilmes"])
def test_a_handle_cannot_match_the_username_or_email(handle):
    with pytest.raises(AccountError, match="public"):
        check_handle(handle, username="Mike", email="mike.wilmes@example.com")


@pytest.mark.parametrize("handle", ["ab", "x" * 25, " Dragon", "Dragon ", "Dragon  Slayer", "-Dragon", "Dra<gon>", "Dragon!"])
def test_a_handle_follows_the_shape_rules(handle):
    with pytest.raises(AccountError, match="3-24"):
        check_handle(handle, username="Mike", email="mike@example.com")


@pytest.mark.parametrize("handle", ["Dragon Slayer", "O'Brien", "x_1", "Mikey"])
def test_ordinary_handles_are_fine(handle):
    check_handle(handle, username="Mike", email="mike@example.com")


@pytest.fixture
def players(app_client):
    def make(db):
        async def both():
            await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
            await create_account(db, "Ann", PASSWORD, email="ann@example.com", confirmed=True)
        return both()

    in_app_db(app_client, make)
    return app_client


def log_in(client, username):
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def test_setting_a_handle(players):
    headers = log_in(players, "Mike")
    answer = players.post("/api/handle", json={"handle": "Dragon Slayer"}, headers=headers)
    assert answer.status_code == 200 and answer.json() == {"handle": "Dragon Slayer"}
    assert players.get("/api/me").json() == {"username": "Mike", "handle": "Dragon Slayer"}
    assert players.post("/api/handle", json={"handle": "dragon SLAYER"}, headers=headers).status_code == 200, \
        "changing your own handle's case is fine"


def test_a_handle_matching_the_login_is_refused(players):
    headers = log_in(players, "Mike")
    for handle in ("mike", "Mike Example Com"):
        answer = players.post("/api/handle", json={"handle": handle}, headers=headers)
        assert answer.status_code == 422, handle
    assert players.get("/api/me").json()["handle"] is None


def test_another_players_handle_is_taken_ignoring_case_and_spacing(players):
    players.post("/api/handle", json={"handle": "Dragon Slayer"}, headers=log_in(players, "Mike"))
    headers = log_in(players, "Ann")
    assert players.post("/api/handle", json={"handle": "dragon  slayer"}, headers=headers).status_code == 422, \
        "two spaces in a row is a shape error"
    assert players.post("/api/handle", json={"handle": "DRAGON SLAYER"}, headers=headers).status_code == 409


def test_setting_a_handle_needs_a_login_and_the_csrf_token(players):
    assert players.post("/api/handle", json={"handle": "Dragon Slayer"}).status_code == 401
    log_in(players, "Mike")
    assert players.post("/api/handle", json={"handle": "Dragon Slayer"}).status_code == 403


def test_the_handle_call_only_takes_a_handle(players):
    headers = log_in(players, "Mike")
    assert players.post("/api/handle", json={"handle": "Dragon Slayer", "username": "x"}, headers=headers).status_code == 422
    assert players.post("/api/handle", json={"handle": 7}, headers=headers).status_code == 422
