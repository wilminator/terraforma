"""The one question the fights ask: may these heroes fight, or has the world retired something they hold?"""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..content.models import Ability, Item, Job
from ..heroes.models import Hero, HeroAbility, HeroItem


async def blocked(session: AsyncSession, hero_ids: Iterable[int]) -> str | None:
    """Why the heroes can't fight yet (None: they can): one of them holds a job, ability or item that is disused, and its player has not
    yet settled it (``disuse.service``). A hero whose player never signs in stays so, and so does the party it is in."""
    ids = list(hero_ids)
    if not ids:
        return None
    held = [
        select(Hero.id).join(Job, Job.id == Hero.job_id).where(Hero.id.in_(ids), Job.disused.is_(True)),
        select(HeroAbility.hero_id).join(Ability, Ability.id == HeroAbility.ability_id).where(HeroAbility.hero_id.in_(ids), Ability.disused.is_(True)),
        select(HeroItem.hero_id).join(Item, Item.id == HeroItem.item_id).where(HeroItem.hero_id.in_(ids), Item.disused.is_(True)),
    ]
    stuck = {hero_id for query in held for hero_id in (await session.scalars(query)).all()}
    if not stuck:
        return None
    name = await session.scalar(select(Hero.name).where(Hero.id.in_(stuck)).order_by(Hero.id).limit(1))
    return f"{name} has to settle what the game has retired before they fight (their player has to sign in)"
