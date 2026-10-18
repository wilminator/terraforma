"""Taxes: the share of a member team's earnings that its alliance keeps.

An alliance sets two rates, in whole percents: ``fight_tax`` on the gold its teams earn from fights and ``sales_tax`` on the gold they
earn selling on a market (``set_fight_tax`` and ``set_sales_tax`` are alliance actions, so a rule can ask for a vote first). The tax is
taken from the earnings as they are paid (``Economy.credit_earnings``) and goes to the alliance's treasury. An alliance taxes its own
members only: a team that left or was removed earns untaxed, and a team in several alliances pays each (never more than it earned).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import FIGHT, SALES
from . import treasury
from .hooks import Alliances
from .models import Alliance, AllianceMember

RATES = {FIGHT: Alliance.fight_tax, SALES: Alliance.sales_tax}


def check(hooks: Alliances, percent: int) -> int:
    if not 0 <= percent <= hooks.max_tax_percent:
        raise treasury.TreasuryError(f"a tax is 0 to {hooks.max_tax_percent} percent")
    return percent


async def set_rate(session: AsyncSession, hooks: Alliances, alliance: Alliance, kind: str, percent: int) -> None:
    check(hooks, percent)
    setattr(alliance, "fight_tax" if kind == FIGHT else "sales_tax", percent)
    await session.flush()


class AllianceTaxman:
    """The ``Economy.taxman`` that pays member teams' alliances. Rounds down, so a small earning can owe nothing."""

    async def withhold(self, session: AsyncSession, team_id: int, kind: str, amount: int) -> int:
        rate = RATES[kind]
        rows = (await session.execute(
            select(Alliance, rate).join(AllianceMember, AllianceMember.alliance_id == Alliance.id)
            .where(AllianceMember.team_id == team_id, rate > 0).order_by(Alliance.id)
        )).all()
        taken = 0
        for alliance, percent in rows:
            tax = min(amount * percent // 100, amount - taken)
            if tax > 0:
                await treasury.put(session, alliance, tax)
                taken += tax
        return taken
