"""Where a game keeps what the heroes do not carry: the game's storage rule.

Whether storage exists, who owns it, how much it holds and when it may be used are the game's decisions, so the engine asks a
``Storage`` (``Game(storage=...)``). The stock rules are ``NoStorage`` (the default: no storage at all), ``TeamStorage``
(one store for each team, shared by its heroes: Vanguard Tavern's way) and ``AccountStorage`` (one store for each account,
shared by all its heroes). A game subclasses ``Storage`` for anything else:

    class Warehouse(TeamStorage):
        capacity = 40

        async def reachable(self, session, hero):
            return await in_town(session, hero)

        async def may_put(self, session, hero, item):
            return None if item.price else "the warehouse only takes things that can be sold"

Every rule that can refuse returns None to allow, or why not (shown to the player as it is). Storage is used outside fights
only: a hero in a running fight cannot put, take or use, whatever the rule says. This is a public interface (the license
exception covers it).
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..heroes.models import Hero, TeamMember


@dataclass(frozen=True)
class Owner:
    """Whose storage it is: ``kind`` is ``"team"`` or ``"account"`` (or a kind of the game's own), ``id`` the row's id."""

    kind: str
    id: int


class Storage:
    #: How many stacks storage holds. (Equipment never stacks; other things stack up to the pack's limit.)
    capacity: int = 24

    async def owner(self, session: AsyncSession, hero: Hero) -> Owner | None:
        """Whose storage this hero uses, or None when they have none (no storage in the game, or a hero on no team)."""
        return None

    async def reachable(self, session: AsyncSession, hero: Hero) -> str | None:
        """None if the hero can get at storage where they stand, or why not (a game may keep it in towns). Always, by default."""
        return None

    async def may_put(self, session: AsyncSession, hero: Hero, item: Item) -> str | None:
        """None if the hero may put this item in storage, or why not. Anything, by default."""
        return None

    async def may_take(self, session: AsyncSession, hero: Hero, item: Item) -> str | None:
        """None if the hero may take this item out of storage, or why not. Anything, by default."""
        return None

    async def may_use(self, session: AsyncSession, hero: Hero, item: Item) -> str | None:
        """None if the hero may use this item straight from storage (outside a fight), or why not. Refused by default: take it out first."""
        return "take it out of storage first"

    async def sharers(self, session: AsyncSession, hero: Hero) -> list[Hero]:
        """The heroes whose packs a delivery fills after the storage is full, in order: the hero's teammates by slot (the hero alone
        when they are on no team). Override it for another circle."""
        members = list((await session.scalars(
            select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id)
            .where(TeamMember.team_id == select(TeamMember.team_id).where(TeamMember.hero_id == hero.id).scalar_subquery())
            .order_by(TeamMember.slot)
        )).all())
        return members or [hero]


class NoStorage(Storage):
    """The default: the game has no storage."""


class TeamStorage(Storage):
    """One store for each team, shared by its heroes. A hero on no team has none."""

    async def owner(self, session: AsyncSession, hero: Hero) -> Owner | None:
        team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
        return None if team_id is None else Owner("team", team_id)


class AccountStorage(Storage):
    """One store for each account, shared by all its heroes."""

    async def owner(self, session: AsyncSession, hero: Hero) -> Owner | None:
        return Owner("account", hero.account_id)

    async def sharers(self, session: AsyncSession, hero: Hero) -> list[Hero]:
        return list((await session.scalars(select(Hero).where(Hero.account_id == hero.account_id).order_by(Hero.id))).all())
