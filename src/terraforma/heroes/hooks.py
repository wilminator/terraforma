"""What a game decides about the heroes of a team once it is saved. A game subclasses ``Roster`` and hands it to
``Game(roster=...)``. This is a public interface (the license exception covers it).

A team is made with its heroes, within the game's ``Rules.team_min`` and ``Rules.team_max``, and a hero never exists without
one. After that a team's heroes are fixed unless the game says otherwise: each rule returns None to allow the change, or why
not (shown to the player as it is). A game that sells these changes (Vanguard Tavern: a Challenge Token purchase) charges in
the rule; the call runs in one transaction, so a change that fails afterwards is not charged. A game that wants them as
ordinary play returns None.

    class Roster(terraforma.heroes.hooks.Roster):
        async def may_move(self, session, account, hero, to_team):
            return None if await spend_tokens(session, account, 5) else "that costs 5 Challenge Tokens"
"""

from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Account
from .models import Hero, Team


class Roster:
    async def may_remove(self, session: AsyncSession, account: Account, hero: Hero) -> str | None:
        """Whether the player may remove a hero from their team for good, or replace them with a new one (the same two
        things: a replacement is a removal and a new hero in the same place). Refused by default."""
        return "heroes cannot be removed or replaced in this game"

    async def may_move(self, session: AsyncSession, account: Account, hero: Hero, to_team: Team) -> str | None:
        """Whether the player may move a hero to another of their teams. Asked once for a move, and once for each hero of an
        exchange (``swap-heroes``). Refused by default."""
        return "heroes cannot be moved between teams in this game"

    async def may_disband(self, session: AsyncSession, account: Account, team: Team) -> str | None:
        """Whether the player may delete a team, which deletes its heroes with it. Allowed by default."""
        return None
