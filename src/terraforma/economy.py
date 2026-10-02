"""Where gold lives, and how it moves: the game's economy.

Gold can live on a hero or on a team, and which one is the game's decision, so the engine asks an ``Economy``
(``Game(economy=...)``) instead of knowing. ``TeamGold`` is DragonStar's way and the default: a hero on a team spends
and earns from the team's gold, and a hero with no team has gold of their own. ``HeroGold`` keeps every hero's gold on
the hero, and splits what a team earns between its heroes. A game overrides ``purse`` and ``credit_team`` for anything
else:

    class Guild(Economy):
        async def purse(self, session, hero):
            ...

This is a public interface (the license exception covers it): the names and signatures are what games build on.

Who may trade is the economy's call too: ``can_trade`` asks its ``trade_policy`` (``trading.policy``), ``WithinTeam`` by
default, and ``related`` is the hook for the relationship a game defines between heroes of different teams.

Gold moves with single statements (``gold = gold + n``, and a debit only where enough is there), so two calls at the same
instant never lose one. A fight's gold is paid once (``credit_fight_gold``).
"""

from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .fights.models import FightRecord
from .heroes.models import Hero, Team, TeamMember
from .trading.policy import Trade, TradePolicy, WithinTeam


class NotEnoughGold(ValueError):
    """The purse holds less than was asked for."""


class Economy:
    #: Who may give gold and items to whom (``trading.policy``).
    trade_policy: TradePolicy = WithinTeam()

    async def can_trade(self, session: AsyncSession, giver: Hero, receiver: Hero, what: Trade) -> bool:
        """Whether $giver may give $receiver $what. Asks the ``trade_policy``; override it for more."""
        return await self.trade_policy.allows(self, session, giver, receiver)

    async def related(self, session: AsyncSession, giver: Hero, receiver: Hero) -> bool:
        """Whether two heroes stand in the game's own relationship (the ``Related`` policy reads it). Nobody does by default."""
        return False

    async def purse(self, session: AsyncSession, hero: Hero) -> Hero | Team:
        """The row whose ``gold`` is this hero's money."""
        raise NotImplementedError

    async def credit_team(self, session: AsyncSession, team_id: int, amount: int) -> None:
        """A team has earned $amount (a fight's gold, say): put it where this economy keeps it."""
        raise NotImplementedError

    async def balance(self, session: AsyncSession, hero: Hero) -> int:
        purse = await self.purse(session, hero)
        await session.refresh(purse, ["gold"])
        return purse.gold

    async def credit(self, session: AsyncSession, hero: Hero, amount: int) -> None:
        if amount < 0:
            raise ValueError("credit a positive amount; debit takes gold away")
        await add_gold(session, await self.purse(session, hero), amount)

    async def debit(self, session: AsyncSession, hero: Hero, amount: int) -> None:
        """Takes $amount from the hero's purse, or raises NotEnoughGold and takes nothing."""
        if amount < 0:
            raise ValueError("debit a positive amount; credit gives gold")
        purse = await self.purse(session, hero)
        table = type(purse)
        taken = await session.execute(update(table).where(table.id == purse.id, table.gold >= amount).values(gold=table.gold - amount))
        if taken.rowcount == 0:
            raise NotEnoughGold("not enough gold")
        await session.refresh(purse, ["gold"])


async def add_gold(session: AsyncSession, purse: Hero | Team, amount: int) -> None:
    table = type(purse)
    await session.execute(update(table).where(table.id == purse.id).values(gold=table.gold + amount))
    await session.refresh(purse, ["gold"])


async def team_of(session: AsyncSession, hero: Hero) -> Team | None:
    return await session.scalar(select(Team).join(TeamMember, TeamMember.team_id == Team.id).where(TeamMember.hero_id == hero.id))


class TeamGold(Economy):
    """DragonStar's way: a team's gold is shared by its heroes. A hero on no team has their own."""

    async def purse(self, session: AsyncSession, hero: Hero) -> Hero | Team:
        return await team_of(session, hero) or hero

    async def credit_team(self, session: AsyncSession, team_id: int, amount: int) -> None:
        team = await session.get(Team, team_id)
        if team is not None:  # a team deleted since the fight began has nowhere to put it
            await add_gold(session, team, amount)


class HeroGold(Economy):
    """Gold is on the hero. What a team earns is split between its heroes (the first heroes by slot get the odd coins)."""

    async def purse(self, session: AsyncSession, hero: Hero) -> Hero | Team:
        return hero

    async def credit_team(self, session: AsyncSession, team_id: int, amount: int) -> None:
        members = list((await session.scalars(select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id).order_by(TeamMember.slot))).all())
        if not members:
            return
        share, extra = divmod(amount, len(members))
        for number, hero in enumerate(members):
            if share + (1 if number < extra else 0):
                await add_gold(session, hero, share + (1 if number < extra else 0))


async def credit_fight_gold(session: AsyncSession, economy: Economy, record: FightRecord, payments: Iterable[tuple[int, int]]) -> bool:
    """Pays a finished fight's gold, ``(team id, amount)`` for each team, through $economy: once. True if it paid now,
    False if the fight's gold had been paid already (nothing is paid twice, however often this is called)."""
    claimed = await session.execute(update(FightRecord).where(FightRecord.id == record.id, FightRecord.gold_paid.is_(False)).values(gold_paid=True))
    if claimed.rowcount == 0:
        return False
    for team_id, amount in payments:
        if amount > 0:
            await economy.credit_team(session, team_id, amount)
    await session.refresh(record, ["gold_paid"])
    return True
