"""What a game decides about NPCs. A game subclasses ``Npcs`` and hands it to ``Game(npcs=...)``. This is a public interface
(the license exception covers it).

    class Npcs(terraforma.npcs.hooks.Npcs):
        async def can_talk(self, session, npc, hero):
            return None if hero.level >= 3 else "the guard waves you off"
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero
from .models import Npc


class Npcs:
    async def can_talk(self, session: AsyncSession, npc: Npc, hero: Hero) -> str | None:
        """Whether the hero may talk to the NPC now: None if so, otherwise why not. By default the hero stands on a counter tile
        or next to one (any of the eight tiles around it), on the NPC's map, and the NPC stands next to the same tile; an NPC
        with no counter is talked to from the tiles around it. A game can ask for more (a level, a quest, a time of day)."""
        if hero.map_id != npc.map_id:
            return "that person is not here"
        near = lambda a, b: max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1  # noqa: E731
        spot, there = (hero.x, hero.y), (npc.x, npc.y)
        if not npc.counter:
            return None if near(spot, there) else "that person is too far away"
        if any(near(spot, tile) and near(there, tile) for tile in map(tuple, npc.counter)):
            return None
        return "stand at the counter to talk"

    async def tag(self, session: AsyncSession, hero: Hero, command: str, parts: list[str]) -> str | None:
        """A tag in an NPC's text the engine does not act on itself: vend, hawk, services, heal, recharge, select_team, resurrect,
        cure, uncurse, and the Yes of an inn. Return the label to go to, ``""`` to go on with the text, or None for not
        handled: the hero's browser is then told to run it as an activity (the shop's calls, say) and to call ``next``
        when it is done. By default nothing is handled."""
        return None
