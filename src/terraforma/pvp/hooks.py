"""Where a party may pick a fight with another party. A game subclasses ``PvpZones`` and hands it to ``Game(pvp=...)``.

The engine's default is that PvP is permitted nowhere. A game opts places in (or out) with ``allows_pvp``. Whether a
particular fight is allowed in a place that permits PvP is ``Rules.may_start_pvp`` (the range window). This is a public
interface (the license exception covers it).

Vanguard Tavern's setup, as an example: towns and instance dungeons are PvE only, everywhere else is PvP.

    class Zones(terraforma.pvp.hooks.PvpZones):
        async def allows_pvp(self, session, map_id, x, y):
            return not (await TOWNS.is_town(session, map_id, x, y) or map_id in INSTANCE_MAPS)
"""

from sqlalchemy.ext.asyncio import AsyncSession


class PvpZones:
    async def allows_pvp(self, session: AsyncSession, map_id: int, x: int, y: int) -> bool:
        """Whether a party may pick a fight with another party at this place. Nowhere, by default."""
        return False
