"""Is an account in an alliance? (Models only, so any module may import it.) Used to decide who still sees an alliance's name after it failed the name screen: its members share it, everybody else sees a neutral label."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Team
from .models import AllianceMember


async def account_in_alliance(session: AsyncSession, account_id: int | None, alliance_id: int) -> bool:
    """Does the account have a team in the alliance? (False for nobody signed in.)"""
    if account_id is None:
        return False
    return await session.scalar(
        select(AllianceMember.id).join(Team, Team.id == AllianceMember.team_id).where(AllianceMember.alliance_id == alliance_id, Team.account_id == account_id).limit(1)
    ) is not None
