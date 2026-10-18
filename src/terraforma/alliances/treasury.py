"""The alliance's treasury: gold that member teams give and the alliance spends.

The balance (``Alliance.gold``) moves here, and the alliance actions that use it are ``service.deposit`` and ``service.spend``. A
deposit is a gift: it leaves the giving team's purse (``Alliances.economy``) and nothing records who gave it or gives it back
when a team leaves. When the alliance dissolves, whatever is left is split between every team that is or was in it, by shares of
how recently each played (``shares``).

Gold moves with single statements, so two calls at the same instant never lose a coin.
"""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from .errors import AllianceError
from ..heroes.models import Team
from .hooks import Alliances
from .models import Alliance, AllianceAlumnus, AllianceMember

MAX_AMOUNT = 10**9


class TreasuryError(AllianceError):
    pass


def _check(amount: int) -> int:
    if not 1 <= amount <= MAX_AMOUNT:
        raise TreasuryError(f"an amount is 1 to {MAX_AMOUNT} gold")
    return amount


async def put(session: AsyncSession, alliance: Alliance, amount: int) -> None:
    """Adds gold to the treasury (for the engine's own parts: a tax, say). The gold must already have left wherever it came from."""
    await session.execute(update(Alliance).where(Alliance.id == alliance.id).values(gold=Alliance.gold + _check(amount)))
    await session.refresh(alliance, ["gold"])


async def take(session: AsyncSession, alliance: Alliance, amount: int) -> None:
    """Takes gold out of the treasury, for the caller to put somewhere, or raises TreasuryError and takes nothing."""
    taken = await session.execute(update(Alliance).where(Alliance.id == alliance.id, Alliance.gold >= _check(amount)).values(gold=Alliance.gold - amount))
    if taken.rowcount == 0:
        raise TreasuryError("the treasury doesn't hold that much")
    await session.refresh(alliance, ["gold"])


# --- when the alliance dissolves ---------------------------------------------------------------------------------------------------------

def shares(hooks: Alliances, last_played_at: int | None, now: int) -> int:
    """12 for a team that played within the last month, one fewer for each further month, none after a year (or if it never played)."""
    if last_played_at is None:
        return 0
    months = max(0, now - last_played_at) // hooks.share_month
    return max(0, hooks.max_shares - months)


async def remember(session: AsyncSession, alliance_id: int, team_id: int) -> None:
    """A team that stops being a member is kept as an alumnus, with its claim on the treasury."""
    if await session.scalar(select(AllianceAlumnus.id).where(AllianceAlumnus.alliance_id == alliance_id, AllianceAlumnus.team_id == team_id)) is None:
        session.add(AllianceAlumnus(alliance_id=alliance_id, team_id=team_id))


async def dissolve(session: AsyncSession, hooks: Alliances, alliance: Alliance) -> dict[int, int]:
    """Pays the treasury out to every team that is or was a member, in proportion to its shares; returns what each got. What the
    shares don't divide (a few coins) goes to the teams with the most shares first, a coin each. With no shares (nobody has played for
    a year), the gold leaves the game's economy."""
    if alliance.gold <= 0:
        return {}
    ids = set((await session.scalars(select(AllianceMember.team_id).where(AllianceMember.alliance_id == alliance.id))).all())
    ids |= set((await session.scalars(select(AllianceAlumnus.team_id).where(AllianceAlumnus.alliance_id == alliance.id))).all())
    now = wallclock.timestamp()
    held = {team_id: shares(hooks, played, now) for team_id, played in (await session.execute(select(Team.id, Team.last_played_at).where(Team.id.in_(ids)))).all()}
    held = {team_id: each for team_id, each in held.items() if each > 0}
    total = sum(held.values())
    if not total:
        return {}
    paid = {team_id: alliance.gold * each // total for team_id, each in held.items()}
    left = alliance.gold - sum(paid.values())
    for team_id in sorted(held, key=lambda each: (-held[each], each))[:left]:
        paid[team_id] += 1
    for team_id, amount in paid.items():
        if amount:
            await hooks.economy.credit_team(session, team_id, amount)
    return paid
