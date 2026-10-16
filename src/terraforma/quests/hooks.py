"""What a game decides about the weekly quest each team is offered. A game subclasses ``WeeklyQuests`` and hands it to
``Game(weekly_quests=...)``. This is a public interface (the license exception covers it).

The engine does the plumbing: one quest per team per week, drawn from the world's random streams so it replays, drawn from the
templates that fit or made up by ``generate``, and every generated one kept so it can be promoted to a template. What a quest *is*
(its goals, its reward, the dungeon it plays in) is the game's: a quest is a ``spec`` dictionary of the game's own that the engine
stores and hands back, never reads.

    class Weekly(terraforma.quests.hooks.WeeklyQuests):
        def generate(self, rng, team_id, level):
            return GeneratedQuest("Rats in the cellar", {"kill": {"rat": level * 2}, "gold": level * 50})

        def scale(self, template, level):
            return {**template.spec, "gold": level * 50}
"""

from dataclasses import dataclass
from random import Random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero, TeamMember


@dataclass(frozen=True)
class GeneratedQuest:
    """A quest made up from nothing: a name (up to 64 characters) and the game's own ``spec``."""

    name: str
    spec: dict


class WeeklyQuests:
    async def level(self, session: AsyncSession, team_id: int) -> int:
        """How strong the team is, as one level: the average level of its heroes, rounded (at least 1; 1 for a team with none)."""
        levels = list((await session.scalars(select(Hero.level).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team_id))).all())
        return max(1, round(sum(levels) / len(levels))) if levels else 1

    async def difficulty(self, session: AsyncSession, team_id: int) -> int:
        """How many levels harder (or, negative, easier) this team's quest is than the team: its relative difficulty. 0 by default."""
        return 0

    def use_template(self, rng: Random, fitting: int) -> bool:
        """Whether the team's quest comes from a template or is generated, when $fitting templates fit its level. By default a template
        whenever one fits. A game that wants a share of generated quests draws from $rng (a stream of the world, so it replays)."""
        return fitting > 0

    def scale(self, template, level: int) -> dict:
        """The quest a template becomes for a team whose quest is at $level: ``template.spec`` (a copy of it) by default. A game scales its
        goals and rewards here."""
        return dict(template.spec)

    def generate(self, rng: Random, team_id: int, level: int) -> GeneratedQuest | None:
        """A quest made up for the team at $level, drawing only from $rng. None (the default) means this game makes none: a team no template
        fits then has no quest that week."""
        return None


class RotatingQuests:
    """What a game decides about the rotating quests (``quests.rotation``): a slot such as a guild's rank S promotion holds one quest at a
    time and changes it after a random while. A game subclasses this and hands it to ``Game(rotating_quests=...)``. This is a public
    interface (the license exception covers it).

    The engine draws a slot's next quest from the templates tagged for the slot (``slot`` in ``quest_templates.json``); only when none is
    active does it call ``generate``. A quest's contents (goals, reward, the dungeon) are the game's own ``spec``, never read here.

        class Rotating(terraforma.quests.hooks.RotatingQuests):
            async def penalty(self, session, team_id, slot, reason):
                if reason == POOR_PERFORMANCE:
                    await ranks.remove_points(session, team_id, slot.guild, 10)   # (a game's own rule)
    """

    def generate(self, rng: Random, slot, seq: int) -> GeneratedQuest | None:
        """A quest made up for $slot's period number $seq (``slot`` is the ``QuestSlot`` row), drawing only from $rng. None (the default) means the
        game makes none: the slot then offers nothing for that period."""
        return None

    async def penalty(self, session: AsyncSession, team_id: int, slot, reason: str) -> None:
        """A team failed a quest of $slot. $reason is ``timeout`` (it ran out of time) or ``poor_performance`` (the game's dialog or dungeon said
        it lost). Does nothing by default: the game decides what failing costs (guild points, gold, a status). Never called when the
        world ended the quest."""


@dataclass(frozen=True)
class Band:
    """A range of party strength (PXP) the world should always have a quest for, and where such quests go. $slot is the rotating quest
    slot (``quests.rotation``) the quests are made for. A band is judged only when it holds at least $min_teams teams."""

    key: str
    slot: str
    pxp_min: int = 0
    pxp_max: int = 0  # (0: no ceiling)
    #: A band is stalled when none of its teams completed a quest for this many seconds (0: stalls are not looked for).
    stall_seconds: int = 0
    min_teams: int = 1


class Needs:
    """What a game decides about the quests the world needs (``quests.needs``). A game subclasses this and hands it to ``Game(needs=...)``.
    This is a public interface (the license exception covers it).

    The engine looks at each band of party strength the game lists. A band has a gap when no quest of its slot fits it, or when none of its
    teams completed a quest for the band's stall time. It then promotes an existing monster (or a gang) to a boss, scaled to the band
    (the engine never makes up a monster: the world works with the monsters it has), and makes a quest of it. The default lists no bands, so
    nothing is made unless the game asks.

        class Wanted(terraforma.quests.hooks.Needs):
            def bands(self):
                return [Band("vanguard-a", "vanguard-rank-a", pxp_min=3000, pxp_max=6000, stall_seconds=30 * 86400)]
            def extras(self, rng, band, monster):
                return {"abilities": ["roar"]}
    """

    def bands(self) -> list[Band]:
        """The bands the game wants quests for, strongest teams last or in any order."""
        return []

    def needs_approval(self, band: Band) -> bool:
        """Whether a quest made for $band waits for an admin to approve it (True, the default) or goes live at once."""
        return True

    def target(self, band: Band) -> int:
        """The party strength a quest for $band should challenge: the band's ceiling, or half as much again as its floor when it has none."""
        return band.pxp_max or band.pxp_min * 3 // 2

    def gang_size(self, rng: Random, band: Band) -> int:
        """How many monsters stand together as the boss: 1 by default (a single boss). The target strength is shared among them."""
        return 1

    def pick(self, rng: Random, band: Band, candidates: list[str]) -> list[str] | None:
        """The monsters (keys) to promote, from $candidates (monsters weak enough to be scaled up to the target and not so weak that the
        scaling would be absurd). None (the default) leaves the choice to the engine, which draws from $rng weighted by strength."""
        return None

    def extras(self, rng: Random, band: Band, monster: str) -> dict:
        """What the game adds to make a boss distinctive, stored with it and never read except ``abilities`` (a list of ability keys the boss
        gains in a fight). Nothing by default."""
        return {}

    def name(self, rng: Random, band: Band, monsters: list[str]) -> str:
        """The new quest's name (up to 64 characters)."""
        return f"Hunt: {', '.join(monsters)}"[:64]


class ManualQuests:
    """What a game decides about the events its admins write (``quests.manual``): an alliance competition, a coliseum tournament. A game
    subclasses this and hands it to ``Game(manual_quests=...)``. This is a public interface (the license exception covers it).

    The engine keeps the events, the entries and the clock; who may enter and what failing costs are the game's. An event's ``kind`` and
    ``spec`` are the game's own, stored and handed back, never read.

        class Events(terraforma.quests.hooks.ManualQuests):
            async def may_enter(self, session, team_id, quest):
                if quest.kind == "alliance" and not await alliances.is_member(session, team_id):
                    return "Only alliance teams may enter."
    """

    async def may_enter(self, session: AsyncSession, team_id: int, quest) -> str | None:
        """None if the team may enter $quest (a ``ManualQuest`` row), otherwise why not. Called after the engine's own checks (the event is
        open, has room, the team has not entered). Anyone may enter by default."""
        return None

    async def penalty(self, session: AsyncSession, team_id: int, quest, reason: str) -> None:
        """A team failed $quest. $reason is ``timeout`` or ``poor_performance``. Does nothing by default. Never called for a cancelled event."""
