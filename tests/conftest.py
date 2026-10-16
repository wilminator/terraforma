"""The engine's own tests use the same fixtures it offers games (terraforma.testing)."""

from datetime import UTC, datetime, timedelta

import pytest

from terraforma import wallclock

pytest_plugins = ["terraforma.testing"]

# Every test starts at this instant, a couple of minutes into an hour. Rate limits count in
# windows aligned to the clock (15 minutes, an hour), so a test that happened to run across a
# window's end would count its attempts in two windows and fail now and then. A fixed start
# means every run counts the same way.
START = datetime(2026, 10, 1, 12, 2, 0, tzinfo=UTC)


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
