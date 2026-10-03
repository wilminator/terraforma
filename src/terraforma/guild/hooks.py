"""What a game decides about the adventurers guild (teams asking to join parties). A game subclasses ``Guild`` and hands it to
``Game(guild=...)``. This is a public interface (the license exception covers it).

    class Guild(terraforma.guild.hooks.Guild):
        async def guest_pass(self, session, team_id):
            return GuestPass("guest", 3600)

        async def may_ask(self, session, team_id, party_id):
            return None if await in_my_faction(session, team_id) else "the guild only brings your faction together"
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alliances.models import AllianceMember
from ..heroes.models import Team
from ..parties.models import PartyTeam


@dataclass(frozen=True)
class GuestPass:
    """How long a team added to a party stays: the standing status it is given (a key in the content's statuses, which can be a
    plain one with no effects) and for how many seconds."""

    status: str
    seconds: int


class Guild:
    async def guest_pass(self, session: AsyncSession, team_id: int) -> GuestPass | None:
        """The pass a team gets when its player adds it to a party (``add_team``): when its time runs out the team leaves the party.
        None, by default: it stays until it leaves."""
        return None

    async def may_ask(self, session: AsyncSession, team_id: int, party_id: int) -> str | None:
        """Whether the team may ask to join the party: None if so, otherwise why not. By default the team must be an ally of
        it: in an alliance with one of the party's teams, or owned by the same player as one of them."""
        team = await session.get(Team, team_id)
        members = list((await session.scalars(select(PartyTeam.team_id).where(PartyTeam.party_id == party_id))).all())
        if team is None or not members:
            return "there is no such party"
        if await session.scalar(select(Team.id).where(Team.id.in_(members), Team.account_id == team.account_id).limit(1)) is not None:
            return None
        mine = select(AllianceMember.alliance_id).where(AllianceMember.team_id == team_id)
        if await session.scalar(select(AllianceMember.id).where(AllianceMember.team_id.in_(members), AllianceMember.alliance_id.in_(mine)).limit(1)) is not None:
            return None
        return "only allies can ask to join that party"
