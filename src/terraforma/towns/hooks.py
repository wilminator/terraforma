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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Monster
from ..fights.build import monster_fighter
from ..fights.models import FightParticipant, FightRecord
from ..fights.rules import Rules
from ..heroes.models import TeamMember
from ..models import Map
from ..world.rng import WorldRng
from ..world.start import HUB_NAME


class Towns:
    async def is_town(self, session: AsyncSession, map_id: int, x: int, y: int) -> bool:
        """Whether this place is a town: a party that comes here is suspended. The hub is, by default: a party formed there waits
        for its teams to be ready (the Leave Town button) before it goes on."""
        game_map = await session.get(Map, map_id)
        return game_map is not None and game_map.name == HUB_NAME

    async def groups(self, session: AsyncSession, party_id: int, team_ids: list[int]) -> list[list[int]]:
        """How a suspended party's teams are grouped, in the order of $team_ids (the party's formation): a list of groups,
        every team in exactly one. A group goes to wait and comes back together. The default is one group per team."""
        return [[team_id] for team_id in team_ids]

    async def may_wait(self, session: AsyncSession, team_id: int) -> str | None:
        """Whether a team may start waiting to leave: None if it may, otherwise why not. By default a team with a hero in a
        running fight may not, but a hero that fled from it is free (``FightParticipant.fled``); a game may ask more or less."""
        busy = await session.scalar(
            select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
            .join(TeamMember, TeamMember.hero_id == FightParticipant.hero_id)
            .where(TeamMember.team_id == team_id, FightRecord.finished.is_(False), FightParticipant.fled.is_(False)).limit(1)
        )
        return "a hero of that team is in a fight" if busy is not None else None

    async def encounter(self, session: AsyncSession, rules: Rules, party_id: int, strength: int, rng: WorldRng, number: int) -> list[str]:
        """The monsters (their keys) the party faces when it leaves the town and is whole again; none means no fight. $strength
        is the party's strength (its fighters' PXP, ``Rules.pxp``), $rng the world's streams and $number how many fights this
        party has had here (so the same party leaving twice meets different monsters). By default: active monsters picked at
        random, one at a time, while the total of their PXP stays within $strength, at least one and at most a full party."""
        keys = sorted((await session.scalars(select(Monster.key).where(Monster.active.is_(True)))).all())
        if not keys:
            return []
        stream = rng.stream("town", "party", party_id, "encounter", number)
        worth: dict[str, int] = {}
        picked, total = [], 0
        for _ in range(rules.party_size * 3):
            if len(picked) >= rules.party_size:
                break
            key = stream.choice(keys)
            if key not in worth:
                worth[key] = rules.pxp(await monster_fighter(session, key, rules))
            if picked and total + worth[key] > strength:
                continue
            picked.append(key)
            total += worth[key]
        return picked
