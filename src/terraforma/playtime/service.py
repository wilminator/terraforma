"""Play time, counted in wall-clock seconds (``wallclock``, as players are the ones waiting).

Two things earn it, and a stretch of time earns once however many ways it is claimed:

- an input: each call a player's input drives (the calls that change something, ``ActingAccount``) credits the next
  ``Rules.play_credit_seconds`` (10), or less if the next such call comes first;
- a fight's playback: from the moment a round was played until the page says it has finished showing it (the
  ``playback-done`` call), at most ``Rules.playback_cap_seconds`` (5 minutes), so a closed tab does not earn.

Reads do not count. The account remembers up to what second it has counted (``counted_to``, never later than now), and a
stretch is credited only for the part beyond it, so a player can never have played longer than the time since they
began. An input's own stretch is settled by the next input (or by a playback that starts after it) and until then it is
added when the total is read (``seconds_played``).
"""

from datetime import datetime

from sqlalchemy import case, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..db.dialect import upsert
from ..fights.rules import Rules
from .models import PlayTime


def _later(a, b):
    """The greater of two SQL values (portable: not every database has GREATEST)."""
    return case((a > b, a), else_=b)


def _earlier(a, b):
    return case((a < b, a), else_=b)


def _gain(start, end):
    """The seconds of the stretch from $start to $end that are beyond what is counted already."""
    begin = _later(PlayTime.counted_to, start)
    return case((end > begin, end - begin), else_=0)


async def _row(session: AsyncSession, account_id: int) -> None:
    await upsert(session, PlayTime.__table__, {"account_id": account_id}, ["account_id"])


async def credit_input(session: AsyncSession, rules: Rules, account_id: int) -> None:
    """A call the player's input drove, now: the stretch the previous input earned is settled (up to now, at most
    ``Rules.play_credit_seconds`` of it), and this one starts. Safe under concurrent calls."""
    now = wallclock.timestamp()
    await _row(session, account_id)
    start = case((PlayTime.last_input_at == 0, now), else_=PlayTime.last_input_at)  # the first input settles nothing
    end = _earlier(start + rules.play_credit_seconds, now)
    # Assigned in this order: MySQL gives a column the new value of the ones assigned before it in the same UPDATE.
    await session.execute(
        update(PlayTime).where(PlayTime.account_id == account_id).ordered_values(
            (PlayTime.seconds, PlayTime.seconds + _gain(start, end)),
            (PlayTime.counted_to, _later(PlayTime.counted_to, end)),
            (PlayTime.last_input_at, _later(PlayTime.last_input_at, now)),
        )
    )


async def credit_playback(session: AsyncSession, rules: Rules, account_id: int, played: datetime) -> None:
    """A round that was played at $played has finished showing on the player's page, now."""
    start = int(played.timestamp())
    end = min(wallclock.timestamp(), start + rules.playback_cap_seconds)
    await _row(session, account_id)
    await session.execute(
        update(PlayTime).where(PlayTime.account_id == account_id).ordered_values(
            (PlayTime.seconds, PlayTime.seconds + _gain(start, end)),
            (PlayTime.counted_to, _later(PlayTime.counted_to, end)),
        )
    )


async def seconds_played(session: AsyncSession, rules: Rules, account_id: int) -> int:
    """The seconds credited to the account: what is settled, and the stretch its latest input earns (0 for one that never acted)."""
    row = (await session.execute(select(PlayTime.seconds, PlayTime.counted_to, PlayTime.last_input_at).where(PlayTime.account_id == account_id))).first()
    if row is None:
        return 0
    seconds, counted_to, last_input_at = row
    if last_input_at == 0:
        return seconds
    return seconds + max(0, last_input_at + rules.play_credit_seconds - max(last_input_at, counted_to))
