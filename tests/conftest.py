"""The engine's own tests use the same fixtures it offers games (terraforma.testing)."""

from datetime import UTC, datetime, timedelta

import pytest

from argon2 import PasswordHasher

from terraforma import wallclock
from terraforma.accounts import service as accounts_service

pytest_plugins = ["terraforma.testing"]

# Every test starts at this instant, a couple of minutes into an hour. Rate limits count in
# windows aligned to the clock (15 minutes, an hour), so a test that happened to run across a
# window's end would count its attempts in two windows and fail now and then. A fixed start
# means every run counts the same way.
START = datetime(2026, 10, 1, 12, 2, 0, tzinfo=UTC)


#: The hasher the site really uses (RFC 9106 defaults: 64 MiB, 3 passes, 4 lanes), about 70 ms a hash or check on a quiet machine and several
#: times that when the tests run in parallel. A test makes an account or signs in several times, and measured over the "heroes" category
#: 61% of all test time went into Argon2. The tests run on this cheap one instead (the same algorithm, Argon2id, and the same hash format,
#: so checking and the "needs rehash" logic behave the same); `real_hasher` below puts the real one back for the test that checks its settings.
PRODUCTION_HASHER = accounts_service._hasher
CHEAP_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)


@pytest.fixture(autouse=True)
def cheap_passwords(monkeypatch):
    """Makes and checks passwords with a cheap Argon2id for the test (the work factor is what costs, and no test depends on it)."""
    monkeypatch.setattr(accounts_service, "_hasher", CHEAP_HASHER)
    monkeypatch.setattr(accounts_service, "_DUMMY_HASH", CHEAP_HASHER.hash("not a real password"))


@pytest.fixture
def real_hasher(monkeypatch, cheap_passwords):
    """The site's own hasher, for the one test that checks its settings."""
    monkeypatch.setattr(accounts_service, "_hasher", PRODUCTION_HASHER)
    return PRODUCTION_HASHER


@pytest.fixture(autouse=True)
def later(monkeypatch):
    """Holds the wall clock still at START; later(seconds) sets it to START plus $seconds."""
    moved = START
    monkeypatch.setattr(wallclock, "now", lambda: moved)

    def move(seconds: int) -> None:
        nonlocal moved
        moved = START + timedelta(seconds=seconds)

    return move


# --- test categories (CI runs each category on each database as its own job) ---------------------------------------------------

#: The categories a test file may declare. A file says which one it belongs to with a line near its top:
#: `pytestmark = pytest.mark.heroes` (or `[pytest.mark.anyio, pytest.mark.heroes]` beside other marks).
#: Nothing else lists the files, so a new test file never edits a shared file (a test checks that every file declares one).
CATEGORIES = ("accounts", "platform", "heroes", "fights", "social")


def pytest_collection_modifyitems(items):
    """Marks a test `e2e` for the browser tests, `uncategorized` when its file declares no category, and `db` when it runs against a database (so `-m "not db"` is the pure ones)."""
    for item in items:
        if "e2e" in item.path.parts:  # the browser tests: CI runs them as their own jobs (browser-tests.yml)
            item.add_marker(pytest.mark.e2e)
        elif not any(item.get_closest_marker(name) for name in CATEGORIES):
            item.add_marker(pytest.mark.uncategorized)
        if "database_url" in item.fixturenames:
            item.add_marker(pytest.mark.db)
