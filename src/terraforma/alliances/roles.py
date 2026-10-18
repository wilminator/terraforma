"""What an alliance's roles of tier 3 may do, as the alliance keeps them (``AllianceRole``).

Queries only, no rules: the rules are ``Alliances.allowed``, which asks here, and the calls that change roles are in ``service``.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AllianceMember, AllianceRole


async def actions(session: AsyncSession, alliance_id: int, role: str) -> set[str]:
    """What the alliance's tier-3 ``role`` may do (nothing, for a role the alliance doesn't have)."""
    held = await session.scalar(select(AllianceRole.actions).where(AllianceRole.alliance_id == alliance_id, AllianceRole.name == role))
    return set(held or ())


async def names(session: AsyncSession, alliance_id: int) -> list[str]:
    """The alliance's tier-3 roles, in the order it made them."""
    return list((await session.scalars(select(AllianceRole.name).where(AllianceRole.alliance_id == alliance_id).order_by(AllianceRole.id))).all())


async def seat_empty(session: AsyncSession, alliance_id: int, commander_role: str) -> bool:
    return await session.scalar(select(AllianceMember.id).where(AllianceMember.alliance_id == alliance_id, AllianceMember.role == commander_role).limit(1)) is None
