"""What a game decides about reach: whether an actor is in range of a target for an action. A game subclasses ``Reach`` and
hands it to ``Game(reach=...)``. This is a public interface (the license exception covers it).

An action is a string: the engine's are ``TALK``, ``INVITE``, ``OPEN``, ``SEARCH``, ``FIGHT`` and ``HELP``, and a game may
add its own. One rule per kind of target, so a game can set NPCs and parties apart: ``npc`` for the people who stand on a
map and ``party`` for other players' parties. Each returns None when the actor is in range, otherwise why not. The calls that
act (talking, and later opening, searching, fighting, helping) ask again when they run, so a list that has gone stale is
refused cleanly.

    class Reach(terraforma.reach.hooks.Reach):
        async def party(self, session, action, actor, party):
            return None if self.distance(...) <= 3 else "too far away"
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero
from ..models import Map
from ..npcs.models import Npc
from ..parties.models import Party

TALK, INVITE, OPEN, SEARCH, FIGHT, HELP = "talk", "invite", "open", "search", "fight", "help"
ACTIONS = (TALK, INVITE, OPEN, SEARCH, FIGHT, HELP)


def distance(found: Map | None, here: tuple[int, int], there: tuple[int, int]) -> int:
    """How many tiles apart two tiles of a map are, diagonals counting as one step; a wrapped edge (``wrap_x``, ``wrap_y``)
    is no edge, so the way round counts when it is shorter."""
    across, down = abs(here[0] - there[0]), abs(here[1] - there[1])
    if found is not None:
        if found.wrap_x and found.width:
            across = min(across, found.width - across)
        if found.wrap_y and found.height:
            down = min(down, found.height - down)
    return max(across, down)


class Reach:
    #: How long, in seconds, a nearby list for an action may be shown before the browser asks again. An action not named
    #: here gets ``default_valid``. A talk is quick; an invitation can wait.
    valid_seconds: dict[str, int] = {TALK: 60, INVITE: 300}
    default_valid: int = 60
    #: How far (in tiles) a party can be from the actor and still be in range for an action: the engine only asks ``party``
    #: about parties inside this many tiles. None asks about every party on the map. A game whose rule reaches farther than
    #: the default raises it.
    party_window: int | None = 1

    def valid_for(self, action: str) -> int:
        return self.valid_seconds.get(action, self.default_valid)

    async def npc(self, session: AsyncSession, action: str, actor: Hero, npc: Npc) -> str | None:
        """Whether the hero is in range of the NPC for the action: None if so, otherwise why not. By default a talk is from a
        counter tile or next to one (any of the eight tiles around it) that the NPC is also next to, and an NPC with no counter
        is talked to from the tiles around it; any other action is from the tiles around the NPC. A game can ask for more (a
        level, a quest, a time of day)."""
        if actor.map_id != npc.map_id:
            return "that person is not here"
        found = await session.get(Map, npc.map_id)
        near = lambda a, b: distance(found, a, b) <= 1  # noqa: E731
        spot, there = (actor.x, actor.y), (npc.x, npc.y)
        if action != TALK or not npc.counter:
            return None if near(spot, there) else "that person is too far away"
        if any(near(spot, tile) and near(there, tile) for tile in map(tuple, npc.counter)):
            return None
        return "stand at the counter to talk"

    async def party(self, session: AsyncSession, action: str, actor: Hero | Party, party: Party) -> str | None:
        """Whether the actor (a hero, or a party) is in range of another party for the action: None if so, otherwise why not.
        By default within one tile of it, on its map."""
        if actor.map_id != party.map_id:
            return "that party is not here"
        found = await session.get(Map, party.map_id)
        return None if distance(found, (actor.x, actor.y), (party.x, party.y)) <= 1 else "that party is too far away"

    async def lists_parties(self, session: AsyncSession, action: str, hero: Hero) -> bool:
        """Whether the nearby list for the action shows other parties after the NPCs. By default it does for a talk and an
        invitation, and not for the rest (a chest is opened, not talked to)."""
        return action in (TALK, INVITE)
