"""The dialog tags that read or change the game's state: items and quests. The engine runs these itself (``script.STATE_TAGS``).

Each answers with the label to go to when its test fails or its change cannot be made, and "" to go on. The party they look
at is the one the talking hero's team acts in right now (``towns.service.acting_party``); a hero on no team stands alone.
"""

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Item
from ..heroes import inventory
from ..heroes.models import Hero, HeroItem, Team, TeamMember
from ..parties import service as parties
from ..parties.models import Party
from ..quests import service as quests
from ..towns import service as towns
from .script import ScriptError


class DialogState:
    def __init__(self, session: AsyncSession, hero: Hero):
        self.session = session
        self.hero = hero
        self._party: towns.ActingParty | None | bool = False  # (False: not asked yet)

    async def __call__(self, command: str, parts: list[str]) -> str:
        return await getattr(self, command)(*parts)

    # --- who -----------------------------------------------------------------------------------------------------------

    async def team_id(self) -> int | None:
        return await self.session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == self.hero.id))

    async def party(self) -> towns.ActingParty | None:
        if self._party is False:
            team_id = await self.team_id()
            self._party = await towns.acting_party(self.session, team_id) if team_id is not None else None
        return self._party

    async def teams(self, scope: str) -> list[int]:
        """The teams a scope speaks of: every team of the party for ``any`` and ``each``, the leading team for ``lead``. A hero
        on no team has none."""
        party = await self.party()
        if party is None:
            return []
        if scope != "lead":
            return list(party.team_ids)
        owners = {team.id: team.account_id for team in (await self.session.scalars(select(Team).where(Team.id.in_(party.team_ids)))).all()}
        lead = next((team_id for team_id in party.team_ids if owners.get(team_id) == party.leader_account_id), None)
        return [] if lead is None else [lead]

    @staticmethod
    def holds(scope: str, results: list[bool]) -> bool:
        """Whether the scope is met: any of them, the leading one (the list is just it), or all of them."""
        if not results:
            return False
        return all(results) if scope == "each" else any(results)

    # --- items ---------------------------------------------------------------------------------------------------------

    async def item(self, key: str) -> Item:
        item = await self.session.scalar(select(Item).where(Item.key == key))
        if item is None:
            raise ScriptError(f"there is no item {key!r}")
        return item

    async def have_item(self, key: str, qty: str, scope: str, label: str) -> str:
        item, need = await self.item(key), int(qty)
        if await self.party() is None:  # a hero on no team stands alone
            teams: list[list[int]] = [[self.hero.id]]
        else:
            teams = []
            for team_id in await self.teams(scope):
                rows = await self.session.scalars(select(TeamMember.hero_id).where(TeamMember.team_id == team_id))
                teams.append(list(rows.all()))
        everyone = [hero_id for heroes in teams for hero_id in heroes]
        held = dict((await self.session.execute(
            select(HeroItem.hero_id, func.sum(HeroItem.qty)).where(HeroItem.hero_id.in_(everyone), HeroItem.item_id == item.id).group_by(HeroItem.hero_id)
        )).all()) if everyone else {}
        has = [any(held.get(hero_id, 0) >= need for hero_id in heroes) for heroes in teams]
        return "" if self.holds(scope, has) else label

    async def add_item(self, key: str, qty: str, label: str) -> str:
        item, count = await self.item(key), int(qty)
        current = await inventory.stacks(self.session, self.hero)
        room = (inventory.MAX_ITEMS - len(current)) * (inventory.MAX_ITEM_QTY if inventory.stackable(item) else 1)
        if inventory.stackable(item):
            room += sum(inventory.MAX_ITEM_QTY - stack.qty for stack, other in current if other.id == item.id)
        if count > room:  # all of it or none: nothing is added that cannot all be
            return label
        await inventory.add_item(self.session, self.hero, key, count)
        return ""

    async def remove_item(self, key: str, qty: str, label: str) -> str:
        item, left = await self.item(key), int(qty)
        if sum(stack.qty for stack, other in await inventory.stacks(self.session, self.hero) if other.id == item.id) < left:
            return label
        while left:
            stack = next(stack for stack, other in await inventory.stacks(self.session, self.hero) if other.id == item.id)
            left -= await inventory.remove_item(self.session, self.hero, stack.position, left)
        return ""

    # --- quests --------------------------------------------------------------------------------------------------------

    async def quests(self, category: str, level: str, op: str, n: str, scope: str, label: str) -> str:
        counts = [await quests.completed(self.session, team_id, category, None if level == "any" else int(level)) for team_id in await self.teams(scope)]
        return "" if self.holds(scope, [quests.compare(op, count, int(n)) for count in counts]) else label

    async def quest_marker(self, quest: str, op: str, value: str, scope: str, label: str) -> str:
        found = [await quests.marker(self.session, team_id, quest) for team_id in await self.teams(scope)]
        return "" if self.holds(scope, [quests.compare(op, each, int(value)) for each in found]) else label

    async def set_quest_marker(self, quest: str, value: str) -> str:
        team_id = await self.team_id()
        if team_id is None:
            raise ScriptError("a hero on no team has no quests: put them on a team")
        await quests.set_marker(self.session, team_id, quest, int(value))
        return ""

    # --- the guild --------------------------------------------------------------------------------------------------------

    async def open_party(self, setting: str, label: str) -> str:
        """Opens (``on``) or closes (``off``) the party to requests from allies; only its leader may. The label if not."""
        party = await self.party()
        if party is None or await parties.leader_account(self.session, party.party_id) != self.hero.account_id:
            return label
        await self.session.execute(update(Party).where(Party.id == party.party_id).values(open=setting == "on"))
        return ""
