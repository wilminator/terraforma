"""Screening the names players type: the game's filter, the provider's name check (fail open), ``name_ok`` and the stamp, and the calls that use them. Nothing is ever refused."""

import base64
import json
import logging

import httpx2 as httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from terraforma.accounts.service import create_account
from terraforma.alliances.models import Alliance
from terraforma.game import Game
from terraforma.heroes.models import Hero, Team
from terraforma.models import Account
from terraforma.names import screen as names
from terraforma.names.screen import NameScreen, Screened, Screener, Verdict, build_screener, public_name, screen_name
from terraforma.settings import ExternalProvider, Settings
from terraforma.testing import in_app_db, run

from .helpers import expect

pytestmark = pytest.mark.heroes

ISSUER = "https://accounts.test"
CHECK = f"{ISSUER}/api/names/check"
PASSWORD = "correct horse battery"
LIST_1 = "2026-11-08T19:55:00Z"
LIST_2 = "2026-11-09T08:00:00Z"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 10.0}}]}


def provider(**changes) -> ExternalProvider:
    return ExternalProvider(name="accounts", label="Accounts", issuer=ISSUER, client_id="game-id", client_secret="game-secret", name_screen=True, **changes)


class Accounts:
    """A fake Accounts server: says "unsafe" for the names in ``unsafe`` and keeps what it was asked."""

    def __init__(self):
        self.unsafe: set[str] = set()
        self.list_changed = LIST_1
        self.respond = None  # a function (request) -> httpx.Response, or an exception to raise, for the next calls
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.respond is not None:
            outcome = self.respond(request)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        if request.url.path.endswith("/check"):
            name = json.loads(request.content)["name"]
            return httpx.Response(200, json={"safe": name not in self.unsafe, "list_changed": self.list_changed})
        return httpx.Response(200, json={"list_changed": self.list_changed})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handle))

    @property
    def calls(self) -> int:
        return len(self.requests)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def accounts():
    return Accounts()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def remote(accounts, clock):
    return NameScreen(provider(), accounts.client(), clock)


class Filter:
    def __init__(self, *words):
        self.words = words

    def is_unsafe(self, name: str) -> bool:
        return any(word in name.lower() for word in self.words)


def check(remote, name="Kim") -> Verdict:
    return run(remote.check(name))


# --- the provider's check ---------------------------------------------------------------------------------------------------

def test_a_safe_name_is_ok_and_an_unsafe_one_is_not(remote, accounts):
    accounts.unsafe = {"Rude"}
    assert check(remote, "Kim") is Verdict.OK
    assert check(remote, "Rude") is Verdict.UNSAFE
    assert remote.list_changed == LIST_1


def test_the_call_is_basic_auth_with_the_client_id_and_secret_and_only_the_name(remote, accounts):
    check(remote, "Kim")
    request = accounts.requests[0]
    assert str(request.url) == CHECK and request.method == "POST"
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"game-id:game-secret").decode()
    assert json.loads(request.content) == {"name": "Kim"}


def test_name_api_replaces_the_default_address(accounts, clock):
    remote = NameScreen(provider(name_api="https://names.test/v1"), accounts.client(), clock)
    check(remote)
    assert str(accounts.requests[0].url) == "https://names.test/v1/check"


@pytest.mark.parametrize("trouble", [httpx.ReadTimeout("slow"), httpx.ConnectError("down"), httpx.Response(500), httpx.Response(429, headers={"Retry-After": "30"}), httpx.Response(200, text="not json"), httpx.Response(200, json={"safe": "yes"})])
def test_trouble_means_unknown(remote, accounts, trouble):
    accounts.respond = lambda request: trouble
    assert check(remote) is Verdict.UNKNOWN
    assert remote.list_changed is None


def test_a_429_is_honoured_for_its_retry_after(remote, accounts, clock):
    accounts.respond = lambda request: httpx.Response(429, headers={"Retry-After": "30"})
    assert check(remote, "One") is Verdict.UNKNOWN
    accounts.respond = None
    clock.now += 29
    assert check(remote, "Two") is Verdict.UNKNOWN and accounts.calls == 1, "still waiting"
    clock.now += 2
    assert check(remote, "Three") is Verdict.OK and accounts.calls == 2


def test_a_refused_login_fails_open_and_logs_one_line_an_hour(remote, accounts, clock, caplog):
    accounts.respond = lambda request: httpx.Response(401)
    with caplog.at_level(logging.DEBUG, logger="terraforma.names"):
        for each in range(3):
            assert check(remote, f"Name {each}") is Verdict.UNKNOWN
            clock.now += 61  # (past the breaker, so each one asks again)
        errors = [record for record in caplog.records if record.levelno == logging.ERROR]
        assert len(errors) == 1 and "client_secret" in errors[0].getMessage()
        clock.now += 3600
        check(remote, "Later")
        assert len([record for record in caplog.records if record.levelno == logging.ERROR]) == 2


def test_an_answer_is_cached_and_a_newer_list_empties_the_cache(remote, accounts):
    assert check(remote, "Kim") is Verdict.OK and check(remote, "  KIM ") is Verdict.OK
    assert accounts.calls == 1, "same name ignoring case and spacing"
    accounts.list_changed = LIST_2
    assert check(remote, "Other") is Verdict.OK and remote.list_changed == LIST_2
    assert check(remote, "Kim") is Verdict.OK
    assert accounts.calls == 3, "the new list voided the cached answer"


def test_a_cached_answer_expires(remote, accounts, clock):
    check(remote)
    clock.now += names.CACHE_SECONDS + 1
    check(remote)
    assert accounts.calls == 2


def test_the_cache_holds_at_most_2000_names(remote, accounts):
    for each in range(names.CACHE_ENTRIES + 5):
        check(remote, f"Name {each}")
    assert len(remote._cache) <= names.CACHE_ENTRIES


def test_an_older_list_time_is_ignored(remote, accounts):
    check(remote, "One")
    accounts.list_changed = "2026-01-01T00:00:00Z"
    check(remote, "Two")
    assert remote.list_changed == LIST_1


def test_five_failures_open_the_breaker_for_a_minute(remote, accounts, clock):
    accounts.respond = lambda request: httpx.Response(500)
    for each in range(5):
        assert check(remote, f"Name {each}") is Verdict.UNKNOWN
    assert accounts.calls == 5
    accounts.respond = None
    clock.now += 59
    assert check(remote, "Quiet") is Verdict.UNKNOWN and accounts.calls == 5, "no call while open"
    clock.now += 2
    assert check(remote, "Back") is Verdict.OK and accounts.calls == 6, "closed after 60 s"
    assert check(remote, "Again") is Verdict.OK


def test_a_success_in_between_keeps_the_breaker_closed(remote, accounts):
    for round_ in range(3):
        accounts.respond = lambda request: httpx.Response(500)
        for each in range(4):
            check(remote, f"Fail {round_} {each}")
        accounts.respond = None
        assert check(remote, f"Fine {round_}") is Verdict.OK


def test_at_most_300_calls_a_minute(remote, accounts, clock):
    for each in range(names.CALLS_PER_MINUTE):
        assert check(remote, f"Name {each}") is Verdict.OK
    assert check(remote, "One too many") is Verdict.UNKNOWN and accounts.calls == 300
    clock.now += 61
    assert check(remote, "Next minute") is Verdict.OK


def test_the_list_is_asked_for_at_most_once_a_minute(remote, accounts, clock):
    assert run(remote.refresh_list()) == LIST_1
    accounts.list_changed = LIST_2
    assert run(remote.refresh_list()) == LIST_1 and accounts.calls == 1
    clock.now += 61
    assert run(remote.refresh_list()) == LIST_2 and accounts.calls == 2
    request = accounts.requests[-1]
    assert request.method == "GET" and str(request.url) == f"{ISSUER}/api/names/list" and not request.content
    assert request.headers["authorization"].startswith("Basic ")


def test_the_name_is_never_logged(remote, accounts, clock, caplog):
    secret_name = "Zxqvjk Wumbo"
    with caplog.at_level(logging.DEBUG):
        for trouble in (httpx.Response(500), httpx.Response(429, headers={"Retry-After": "1"}), httpx.Response(401), httpx.ReadTimeout("slow"), httpx.Response(200, json={"safe": "maybe"})):
            accounts.respond = lambda request, trouble=trouble: trouble
            for each in range(6):
                clock.now += 100
                check(remote, secret_name)
        accounts.respond = None
        check(remote, secret_name)
        for record in caplog.records:
            text = record.getMessage() + str(record.args) + str(record.exc_info)
            assert "zxqvjk" not in text.lower() and "wumbo" not in text.lower()
    assert caplog.records


# --- the two judges ---------------------------------------------------------------------------------------------------------

def screening(local=None, remote=None):
    return Screener(local, remote)


def test_nothing_to_screen_with_is_not_checked():
    assert run(screen_name(None, "Anything")) == Screened(None, None)
    assert run(screen_name(screening(), "Anything")) == Screened(None, None)


def test_the_local_filter_alone_says_not_ok_without_a_call(accounts, remote):
    assert run(screen_name(screening(Filter("rude"), remote), "Rude Dude")) == Screened(False, None)
    assert accounts.calls == 0


def test_the_provider_alone_says_not_ok_with_the_list_time(accounts, remote):
    accounts.unsafe = {"Nope"}
    assert run(screen_name(screening(Filter("rude"), remote), "Nope")) == Screened(False, LIST_1)


def test_ok_gives_true_and_the_stamp_and_unknown_gives_none(accounts, remote):
    both = screening(Filter("rude"), remote)
    assert run(screen_name(both, "Kim")) == Screened(True, LIST_1)
    accounts.respond = lambda request: httpx.Response(500)
    assert run(screen_name(both, "Lee")) == Screened(None, None)
    assert run(screen_name(screening(Filter("rude")), "Kim")) == Screened(None, None), "a local filter alone that does not object checked nothing"


def test_a_screened_result_is_frozen():
    with pytest.raises(AttributeError):
        Screened(True, None).name_ok = False  # type: ignore[misc]


# --- the one rule for showing a name -----------------------------------------------------------------------------------------

@pytest.mark.parametrize(("name_ok", "own", "shown"), [
    (False, True, "Rude"), (True, True, "Rude"), (None, True, "Rude"),
    (True, False, "Rude"), (None, False, "Rude"), (False, False, "Hero"),
])
def test_public_name(name_ok, own, shown):
    assert public_name("Rude", name_ok, "Hero", own=own) == shown


def test_public_name_defaults_to_another_viewer():
    assert public_name("Rude", False, "Hero") == "Hero"


def test_the_screener_is_built_only_when_there_is_something_to_ask(accounts):
    plain = Settings(session_secret="s" * 32)
    on = Settings(session_secret="s" * 32, external_providers=[provider()])
    assert build_screener(plain, None, accounts.client) is None
    assert build_screener(plain, Game(name="G"), accounts.client) is None
    assert build_screener(plain, Game(name="G", name_screen=Filter("x")), accounts.client).local is not None
    assert build_screener(on, None, accounts.client).remote is not None
    assert accounts.calls == 0


# --- settings ---------------------------------------------------------------------------------------------------------------

def test_name_screen_is_off_by_default():
    assert ExternalProvider(name="a", label="A", issuer=ISSUER, client_id="i", client_secret="s").name_screen is False


def test_name_api_must_be_https_and_goes_with_name_screen():
    assert provider(name_api="https://names.test/api").name_api == "https://names.test/api"
    for bad in ("http://names.test/api", "ftp://names.test", "names.test"):
        with pytest.raises(ValidationError):
            provider(name_api=bad)
    with pytest.raises(ValidationError):
        ExternalProvider(name="a", label="A", issuer=ISSUER, client_id="i", client_secret="s", name_api="https://names.test/api")


def test_only_openid_connect_providers_screen_names():
    with pytest.raises(ValidationError):
        ExternalProvider(name="steam", label="Steam", kind="steam", name_screen=True)
    with pytest.raises(ValidationError):
        ExternalProvider(name="fb", label="FB", kind="facebook", client_id="i", client_secret="s", name_screen=True)
    with pytest.raises(ValidationError):
        ExternalProvider(name="a", label="A", issuer="", client_id="i", client_secret="s", name_screen=True)


def test_at_most_one_provider_screens_names():
    other = ExternalProvider(name="other", label="Other", issuer="https://other.test", client_id="i", client_secret="s")
    Settings(session_secret="s" * 32, external_providers=[provider(), other])
    with pytest.raises(ValidationError):
        Settings(session_secret="s" * 32, external_providers=[provider(), other.model_copy(update={"name_screen": True})])


# --- through the real calls --------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


@pytest.fixture
def client(app_client, accounts, clock):
    app_client.app.state.screener = Screener(Filter("badword"), NameScreen(provider(), accounts.client(), clock))
    return app_client


@pytest.fixture
def headers(client):
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def columns(client, model, name):
    """[(name_ok, screened_at_list)] of the rows named $name."""
    async def read(db):
        column = model.handle if model is Account else model.name
        rows = (await db.execute(select(model.name_ok, model.screened_at_list).where(column == name))).all()
        return [tuple(row) for row in rows]

    return in_app_db(client, read)


def make_team(client, headers, name, hero="Aria"):
    return client.post("/api/teams", json={"name": name, "heroes": [{"name": hero, "job": "fighter"}]}, headers=headers)


def test_a_hero_and_team_made_with_safe_names_are_ok_and_carry_the_stamp(client, headers):
    expect(make_team(client, headers, "Alpha"), 201)
    assert columns(client, Hero, "Aria") == [(True, LIST_1)] and columns(client, Team, "Alpha") == [(True, LIST_1)]


def test_unsafe_names_for_a_new_team_and_its_hero_are_saved_not_ok(client, headers, accounts):
    accounts.unsafe = {"Rude hero", "Rude team"}
    expect(make_team(client, headers, "Alpha", hero="Rude hero"), 201)
    expect(make_team(client, headers, "Rude team"), 201)
    expect(make_team(client, headers, "Badword team", hero="Badword hero"), 201)
    assert columns(client, Hero, "Rude hero") == [(False, LIST_1)] and columns(client, Team, "Alpha") == [(True, LIST_1)]
    assert columns(client, Team, "Rude team") == [(False, LIST_1)]
    assert columns(client, Team, "Badword team") == [(False, None)], "the game's filter: not ok, no stamp"
    assert columns(client, Hero, "Badword hero") == [(False, None)]
    assert len(client.get("/api/teams").json()) == 3 and len(client.get("/api/heroes").json()) == 3


def test_a_filtered_name_makes_no_network_call(client, headers, accounts):
    expect(make_team(client, headers, "Badword team", hero="Badword hero"), 201)
    assert accounts.calls == 0


def test_a_new_and_a_replacement_hero_on_a_team_are_saved_with_their_verdict(client, headers, accounts, monkeypatch):
    from terraforma.heroes.hooks import Roster

    async def yes(self, *args):
        return None

    monkeypatch.setattr(Roster, "may_remove", yes)
    team = expect(make_team(client, headers, "Alpha"), 201).json()
    accounts.unsafe = {"Rude", "Rude too"}
    expect(client.post(f"/api/teams/{team['id']}/heroes", json={"name": "Rude", "job": "fighter"}, headers=headers), 201)
    expect(client.post(f"/api/teams/{team['id']}/heroes", json={"name": "Nice", "job": "fighter"}, headers=headers), 201)
    assert columns(client, Hero, "Rude") == [(False, LIST_1)] and columns(client, Hero, "Nice") == [(True, LIST_1)]
    rude = next(each for each in client.get("/api/heroes").json() if each["name"] == "Rude")
    expect(client.post(f"/api/teams/{team['id']}/heroes/{rude['id']}/replace", json={"name": "Rude too", "job": "fighter"}, headers=headers), 201)
    assert columns(client, Hero, "Rude too") == [(False, LIST_1)] and columns(client, Hero, "Rude") == []


def test_renaming_a_hero_overwrites_the_verdict_both_ways(client, headers, accounts):
    expect(make_team(client, headers, "Alpha"), 201)
    hero = client.get("/api/heroes").json()[0]
    rename = f"/api/heroes/{hero['id']}/rename"
    accounts.unsafe = {"Rude"}
    expect(client.post(rename, json={"name": "Rude"}, headers=headers), 200)
    assert columns(client, Hero, "Rude") == [(False, LIST_1)]
    accounts.list_changed = LIST_2
    expect(client.post(rename, json={"name": "Brin"}, headers=headers), 200)
    assert columns(client, Hero, "Brin") == [(True, LIST_2)], "unsafe to clean flips to true"
    expect(client.post(rename, json={"name": "Rude"}, headers=headers), 200)
    assert columns(client, Hero, "Rude") == [(False, LIST_2)], "clean to unsafe flips to false"
    expect(client.post(rename, json={"name": "Badword"}, headers=headers), 200)
    assert columns(client, Hero, "Badword") == [(False, None)]


def test_renaming_a_team_overwrites_the_verdict_both_ways(client, headers, accounts):
    team = expect(make_team(client, headers, "Old name"), 201).json()
    rename = f"/api/teams/{team['id']}/rename"
    accounts.unsafe = {"Old name", "Rude"}
    expect(client.post(rename, json={"name": "Rude"}, headers=headers), 200)
    assert columns(client, Team, "Rude") == [(False, LIST_1)]
    expect(client.post(rename, json={"name": "Fresh"}, headers=headers), 200)
    assert columns(client, Team, "Fresh") == [(True, LIST_1)]
    expect(client.post(rename, json={"name": "Badword"}, headers=headers), 200)
    assert columns(client, Team, "Badword") == [(False, None)]


def test_an_alliance_is_founded_with_any_name(client, headers, accounts):
    team = expect(make_team(client, headers, "Alpha"), 201).json()
    accounts.unsafe = {"Rude"}
    expect(client.post("/api/alliances", json={"team_id": team["id"], "name": "Rude"}, headers=headers), 201)
    assert columns(client, Alliance, "Rude") == [(False, LIST_1)]
    accounts.unsafe = set()
    expect(client.post("/api/alliances", json={"team_id": team["id"], "name": "Nice"}, headers=headers), 201)
    assert columns(client, Alliance, "Nice") == [(True, LIST_1)]
    expect(client.post("/api/alliances", json={"team_id": team["id"], "name": "Badword"}, headers=headers), 201)
    assert columns(client, Alliance, "Badword") == [(False, None)]


def test_a_handle_is_set_with_any_name_and_a_reset_flips_the_verdict(client, headers, accounts):
    accounts.unsafe = {"Rude one"}
    expect(client.post("/api/handle", json={"handle": "Rude one"}, headers=headers), 200)
    assert columns(client, Account, "Rude one") == [(False, LIST_1)]
    expect(client.post("/api/handle", json={"handle": "Nice one"}, headers=headers), 200)
    assert columns(client, Account, "Nice one") == [(True, LIST_1)]
    expect(client.post("/api/handle", json={"handle": "Badword one"}, headers=headers), 200)
    assert columns(client, Account, "Badword one") == [(False, None)]


def test_unknown_is_saved_and_leaves_name_ok_empty(client, headers, accounts):
    accounts.respond = lambda request: httpx.Response(503)
    team = expect(make_team(client, headers, "Alpha"), 201).json()
    expect(client.post("/api/handle", json={"handle": "Nice one"}, headers=headers), 200)
    assert columns(client, Team, "Alpha") == [(None, None)] and columns(client, Hero, "Aria") == [(None, None)] and columns(client, Account, "Nice one") == [(None, None)]
    expect(client.post("/api/alliances", json={"team_id": team["id"], "name": "Nice"}, headers=headers), 201)
    assert columns(client, Alliance, "Nice") == [(None, None)]
    expect(client.post(f"/api/teams/{team['id']}/rename", json={"name": "Badword"}, headers=headers), 200)  # (the game's filter still speaks)
    assert columns(client, Team, "Badword") == [(False, None)]


def test_without_a_screener_nothing_changes(app_client):
    assert app_client.app.state.screener is None
    in_app_db(app_client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    token = app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]
    expect(make_team(app_client, {"X-CSRF-Token": token}, "Badword"), 201)
    assert columns(app_client, Team, "Badword") == [(None, None)]
