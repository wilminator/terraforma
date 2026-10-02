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
