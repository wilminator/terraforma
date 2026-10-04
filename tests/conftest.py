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

#: Which category every test file belongs to. A new test file must be listed here (a test checks that).
CATEGORIES = {
    "accounts": ["accounts", "handles", "keys", "twofa", "mail", "logscrub", "settings"],
    "platform": ["app", "categories", "content", "database", "housekeeping", "migrations", "maps", "walking", "parallel", "seed", "world"],
    "heroes": ["heroes", "inventory", "field_use", "parties", "towns", "economy", "trading", "drops", "pending_drops", "challenge_tokens", "pvp", "npc_script", "npcs", "quests", "npc_state", "market", "inn", "guild", "standing", "token_tags"],
    "fights": ["round_time", "fight_rules", "fight_store", "live_fights", "experience", "rewards", "ai", "ai_statuses", "statuses"],
    "social": ["relations", "alliances", "ballots", "ratings", "profiles"],
}
CATEGORY_OF_FILE = {f"test_{name}": category for category, names in CATEGORIES.items() for name in names}


def pytest_collection_modifyitems(items):
    """Marks every test with its category, and with `db` when it runs against a database (so `-m "not db"` is the pure ones)."""
    for item in items:
        category = CATEGORY_OF_FILE.get(item.path.stem)
        item.add_marker(getattr(pytest.mark, category or "uncategorized"))
        if "database_url" in item.fixturenames:
            item.add_marker(pytest.mark.db)
