"""What a game decides about what NPCs do. A game subclasses ``Npcs`` and hands it to ``Game(npcs=...)``. This is a public
interface (the license exception covers it). Who may talk to an NPC from where is ``terraforma.reach.hooks.Reach.npc``.

    class Npcs(terraforma.npcs.hooks.Npcs):
        async def tag(self, session, hero, command, parts):
            return "" if command == "heal" else None
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero


class Npcs:
    async def tag(self, session: AsyncSession, hero: Hero, command: str, parts: list[str]) -> str | None:
        """A tag in an NPC's text the engine does not act on itself: vend, hawk, services, heal, recharge, select_team, resurrect,
        cure, uncurse, and the Yes of an inn. Return the label to go to, ``""`` to go on with the text, or None for not
        handled: the hero's browser is then told to run it as an activity (the shop's calls, say) and to call ``next``
        when it is done. By default nothing is handled."""
        return None
