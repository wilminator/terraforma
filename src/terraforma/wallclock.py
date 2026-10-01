"""Real-world time, for security: token expiry and rate limits.

Not the game's time: the world clock (models.World.tick) is for that.
Everything that needs the wall clock calls ``wallclock.now()``, so tests
can move time forward by replacing it.
"""

from datetime import UTC, datetime


def now() -> datetime:
    return datetime.now(UTC)


def timestamp() -> int:
    return int(now().timestamp())
