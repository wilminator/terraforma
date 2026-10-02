"""Who may trade with whom: the stock policies a game's economy picks from.

``Economy.can_trade`` asks its ``trade_policy``. A policy is one small class with one question, ``allows``, so a
game combines them (``AnyOf(WithinTeam(), Related())``) or writes its own:

    class Guildmates(TradePolicy):
        async def allows(self, economy, session, giver, receiver):
            ...

The stock policies are ``NoOne``, ``Anyone``, ``WithinTeam`` (the default), ``WithinParty`` (the same team, or
teams in the same party) and ``Related`` (whatever the economy's ``related`` says: the hook a game fills in for the
parties its heroes deal with). This is a public interface (the license exception covers it).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero, TeamMember
from ..parties.models import PartyTeam


@dataclass(frozen=True)
class Trade:
    """What is being given: gold (``amount``) or an item (``item`` key, ``qty``)."""

    kind: str  # "gold" or "item"
    amount: int = 0
    item: str = ""
    qty: int = 0


async def team_id_of(session: AsyncSession, hero: Hero) -> int | None:
    return await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))


async def party_id_of(session: AsyncSession, team_id: int) -> int | None:
    return await session.scalar(select(PartyTeam.party_id).where(PartyTeam.team_id == team_id))


class TradePolicy:
    async def allows(self, economy, session: AsyncSession, giver: Hero, receiver: Hero) -> bool:
        raise NotImplementedError


class NoOne(TradePolicy):
    """No trading at all."""

    async def allows(self, economy, session, giver, receiver) -> bool:
        return False


class Anyone(TradePolicy):
    async def allows(self, economy, session, giver, receiver) -> bool:
        return True


class WithinTeam(TradePolicy):
    """Heroes on the same team (a hero on no team trades with no one)."""

    async def allows(self, economy, session, giver, receiver) -> bool:
        team = await team_id_of(session, giver)
        return team is not None and team == await team_id_of(session, receiver)


class WithinParty(TradePolicy):
    """Heroes on the same team, or on teams in the same party."""

    async def allows(self, economy, session, giver, receiver) -> bool:
        mine, theirs = await team_id_of(session, giver), await team_id_of(session, receiver)
        if mine is None or theirs is None:
            return False
        if mine == theirs:
            return True
        party = await party_id_of(session, mine)
        return party is not None and party == await party_id_of(session, theirs)


class Related(TradePolicy):
    """Heroes the game's economy calls related (``Economy.related``): nobody, until a game says otherwise."""

    async def allows(self, economy, session, giver, receiver) -> bool:
        return await economy.related(session, giver, receiver)


class AnyOf(TradePolicy):
    """Allowed when any of the policies allows it."""

    def __init__(self, *policies: TradePolicy):
        self.policies = policies

    async def allows(self, economy, session, giver, receiver) -> bool:
        for policy in self.policies:
            if await policy.allows(economy, session, giver, receiver):
                return True
        return False
