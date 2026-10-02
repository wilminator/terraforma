"""What a game decides about towns. A game subclasses ``Towns`` and hands it to ``Game(towns=...)``.

In a *town* (the game says which places are: all towns, or only an inn) a party is suspended: it comes apart into
individual teams (or into groups of teams, which a game can build, for example a player's own teams kept together
because the player paid for it), and each team does its own shopping and resting. A team that goes to leave
waits for the rest of its party; when every team is waiting, the party is put back as one unit in the formation it
had. This is a public interface (the license exception covers it).

    class Towns(terraforma.towns.hooks.Towns):
        async def is_town(self, session, map_id, x, y):
            return (x, y) in TOWN_TILES
"""

from sqlalchemy.ext.asyncio import AsyncSession


class Towns:
    async def is_town(self, session: AsyncSession, map_id: int, x: int, y: int) -> bool:
        """Whether this place is a town: a party that comes here is suspended. Nowhere is, by default."""
        return False

    async def groups(self, session: AsyncSession, party_id: int, team_ids: list[int]) -> list[list[int]]:
        """How a suspended party's teams are grouped, in the order of $team_ids (the party's formation): a list of groups,
        every team in exactly one. A group goes to wait and comes back together. The default is one group per team."""
        return [[team_id] for team_id in team_ids]

    async def may_wait(self, session: AsyncSession, team_id: int) -> str | None:
        """Whether a team may start waiting to leave: None if it may, otherwise why not (it is in a fight, say)."""
        return None
