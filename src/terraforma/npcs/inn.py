"""Resting at an inn: what the Yes of an ``inn,price,label`` tag does when the game's ``Npcs.tag`` leaves it to the engine.

A team rests when its purse pays the price: every hero of the team rests through the game's ``Rules.rest`` (full HP and MP by
default, the dead revived as the game decides) and the price is taken once per team, from the talking hero's purse, through the game's economy. By default
each team pays its own: the talking hero's team rests and pays, and a team that cannot pay is told so and the talk goes on.
A game can let the leading team treat its party: ``Inn.leader_pays`` says so, and then the leading team's purse pays for the
teams acting with it (the leader's own first, then in formation order), as many as it can afford. This is a public interface
(the license exception covers it).

    class Generous(terraforma.npcs.inn.Inn):
        async def leader_pays(self, session, hero, price):
            return True
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import Economy, NotEnoughGold
from ..fights.rules import Rules
from ..heroes.models import Hero, TeamMember
from ..heroes.service import rest_hero
from .script import BadAnswer
from .state import DialogState


class Inn:
    async def leader_pays(self, session: AsyncSession, hero: Hero, price: int) -> bool:
        """Whether the leading team pays for every team acting with it, when its player talks to the innkeeper. Never by
        default: each team pays its own."""
        return False


async def _heroes(session: AsyncSession, team_id: int) -> list[Hero]:
    rows = await session.scalars(select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot))
    return list(rows.all())


async def rest(session: AsyncSession, inn: Inn, rules: Rules, economy: Economy, hero: Hero, price: int) -> str:
    """The hero says Yes at an inn costing $price a team. Returns ``""`` (go on with the text). Raises ``BadAnswer`` (the talk
    stays where it is) when no team could pay. A hero on no team rests alone and pays from their own purse."""
    state = DialogState(session, hero)
    team_id = await state.team_id()
    if team_id is None:
        groups = [[hero]]
    else:
        lead = await state.teams("lead")
        order = [team_id]
        if lead and lead[0] == team_id and await inn.leader_pays(session, hero, price):
            order += [other for other in await state.teams("any") if other != team_id]
        groups = [members for members in [await _heroes(session, each) for each in order] if members]
    rested = 0
    for members in groups:
        try:
            async with session.begin_nested():
                await economy.debit(session, hero, price)  # the talking hero's purse, which is their team's under TeamGold
        except NotEnoughGold:
            continue
        for member in members:
            rest_hero(member, rules)
        rested += 1
    if not rested:
        raise BadAnswer(f"that costs {price} gold, and there is not enough gold")
    await session.flush()
    return ""
