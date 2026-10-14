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
