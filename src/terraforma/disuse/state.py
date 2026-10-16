"""The dialog tags only a force-disuse scene can run (``set_job``, ``lost``): ``DialogState`` with the hero's loss behind them."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Job
from ..economy import Economy
from ..fights.rules import Rules
from ..heroes import inventory
from ..heroes.models import Hero
from ..npcs.script import ScriptError
from ..npcs.state import DialogState
from . import service
from .hooks import Disuse
from .models import ABILITY, JOB


class DisuseState(DialogState):
    def __init__(self, session: AsyncSession, hero: Hero, disuse: Disuse, economy: Economy | None = None, rules: Rules | None = None):
        super().__init__(session, hero, economy, rules)
        self.disuse = disuse

    async def set_job(self, key: str, label: str) -> str:
        """The hero takes the job, if the game offers it (``Disuse.jobs``): its level, experience and stats stay as they are, it learns what
        the job grants at its level, and its old job is noted with it. The label if the job is not offered."""
        found = await service.lost(self.session, self.hero)
        if found.job is None:
            raise ScriptError("set_job: the hero's job is not disused, so there is nothing to change")
        job = await self.session.scalar(select(Job).where(Job.key == key))
        if job is None:
            raise ScriptError(f"there is no job {key!r}")
        if key not in {offered.key for offered in await self.disuse.jobs(self.session, self.hero, found)} or job.disused or not job.active:
            return label
        await service.note(self.session, self.hero, JOB, found.job[0], 1)
        self.hero.job_id = job.id
        await self.session.flush()
        await inventory.grant_abilities(self.session, self.hero)
        return ""

    async def lost(self, kind: str, label: str) -> str:
        found = await service.lost(self.session, self.hero)
        left = found.job is not None if kind == JOB else bool(found.abilities) if kind == ABILITY else bool(found.items)
        return "" if left else label
