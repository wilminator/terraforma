"""Where a party may pick a fight with another party. A game subclasses ``PvpZones`` and hands it to ``Game(pvp=...)``.

The engine's default follows the map: a zone's ``pvp`` flag (maps.json) says, and a map with no zones permits it nowhere. A game can decide otherwise with ``allows_pvp``. Whether a
particular fight is allowed in a place that permits PvP is ``Rules.may_start_pvp`` (the range window). This is a public
interface (the license exception covers it).

Vanguard Tavern's setup, as an example: towns and instance dungeons are PvE only, everywhere else is PvP.

    class Zones(terraforma.pvp.hooks.PvpZones):
        async def allows_pvp(self, session, map_id, x, y):
            return not (await TOWNS.is_town(session, map_id, x, y) or map_id in INSTANCE_MAPS)
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Map


class PvpZones:
    async def allows_pvp(self, session: AsyncSession, map_id: int, x: int, y: int) -> bool:
        """Whether a party may pick a fight with another party at this place. By default the zone there says (its ``pvp`` flag)."""
        found = await session.get(Map, map_id)
        return found is not None and found.zone(x, y)["pvp"]
