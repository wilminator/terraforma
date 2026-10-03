"""Starting a fight against another party: the place's flag, then the rules' range window."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.rules import Rules
from .hooks import PvpZones


def party_pxp(rules: Rules, fighters) -> int:
    """A party's strength: the sum of its fighters' potential experience."""
    return sum(rules.pxp(fighter) for fighter in fighters)


async def refusal(session: AsyncSession, zones: PvpZones, rules: Rules, attacker, target, map_id: int, x: int, y: int) -> str | None:
    """Why the party of fighters $attacker may not pick a fight with $target at this place, or None if it may."""
    allowed = await zones.allows_pvp(session, map_id, x, y)
    return rules.may_start_pvp(party_pxp(rules, attacker), party_pxp(rules, target), allowed)
