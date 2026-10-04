"""Moving a party about on the map: the one place a party's tile changes, so its heroes follow it."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.models import FightParticipant, FightRecord
from ..heroes.models import Hero
from ..models import Map
from ..parties import service as parties
from ..parties.models import Party


async def in_fight(session: AsyncSession, party: Party) -> bool:
    """Whether any hero of the party is in a fight that is still running."""
    heroes = await parties.hero_ids(session, party.id)
    return heroes != [] and await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_(heroes), FightRecord.finished.is_(False)).limit(1)
    ) is not None


async def move_heroes(session: AsyncSession, party: Party) -> None:
    """Puts the party's heroes where the party stands (a party's tile is the one that moves; its heroes follow)."""
    heroes = await parties.hero_ids(session, party.id)
    if heroes:
        await session.execute(update(Hero).where(Hero.id.in_(heroes)).values(map_id=party.map_id, x=party.x, y=party.y))


async def relocate(session: AsyncSession, party: Party, game_map: Map, x: int, y: int) -> None:
    """The party appears at a tile of a map (a door, a portal): its route is dropped and, being new on that map, its steps
    start over. The caller checked the tile."""
    party.map_id, party.x, party.y = game_map.id, x, y
    party.route, party.steps = None, 0
    await session.flush()
    await move_heroes(session, party)
