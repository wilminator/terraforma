"""Using the things that stand on a map: a hero is in reach of a chest or a door (the game's ``Reach.map_object``, for the
object's own action) and its script runs, the same way an NPC's dialog does (``npcs.service``): the server decides everything,
and the page shows what it is told. A script is dialog text, so a chest can ``add_item`` and a door can ``warp``.
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import Economy
from ..heroes.models import Hero
from ..npcs import service as npcs
from ..npcs.hooks import Npcs
from ..reach.hooks import Reach
from .models import EDGE, MapObject


class NoSuchObject(npcs.NpcError):
    """There is no such thing here."""


async def use(session: AsyncSession, hooks: Npcs, reach: Reach, hero: Hero, object_id: int, rest: npcs.Rest | None = None, economy: Economy | None = None) -> dict:
    """The hero uses the object (leaving any other conversation): what its script says first, and what it asks. Refused unless
    the hero is in reach of it for its action and out of a fight. The answer is a conversation frame, with ``window`` true
    only when the script asked something of the hero (the page opens its dialog window then)."""
    found = await session.get(MapObject, object_id)
    if found is None or found.kind == EDGE:
        raise NoSuchObject("there's no such thing")
    await npcs.may_talk(session, reach, found, hero)
    return await npcs.start(session, hooks, hero, found, rest, economy)
