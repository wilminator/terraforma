"""Quest instancing. A game's quest picks a mode: ``PUBLIC`` (no instance: the quest's map is shared by everyone, even a
dungeon), ``PRIVATE`` (a new instance for each party, which sees itself as the only one there) or ``SHARED`` (several parties
meet in one instance, up to a cap; they may cooperate or compete, but only one can complete the quest).

Parties in different instances (or one in an instance and one outside) do not see each other: ``same_place`` is what the
nearby list and PvP ask. A party in a shared instance may fight any other party of that instance whatever its strength
(``Rules.may_fight_in_shared_instance``)."""

from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import wallclock
from ..fights.build import party_side
from ..fights.rules import Rules
from ..maps import travel
from ..models import Map
from ..parties import service as parties
from ..towns import service as towns
from .models import InstanceParty, QuestInstance

PUBLIC, PRIVATE, SHARED = "public", "private", "shared"
MODES = (PUBLIC, PRIVATE, SHARED)
OPEN, WON = "open", "won"


class InstanceError(ValueError):
    """Why a party can't do that: the message says what."""


async def instance_of(session: AsyncSession, party_id: int | None) -> QuestInstance | None:
    """The instance the party is in (None: it is out in the world, as is no party at all, a hero on no team)."""
    if party_id is None:
        return None
    return await session.scalar(
        select(QuestInstance).join(InstanceParty, InstanceParty.instance_id == QuestInstance.id).where(InstanceParty.party_id == party_id)
    )


async def same_place(session: AsyncSession, one_id: int | None, other_id: int | None) -> bool:
    """Whether two parties can meet: both are out in the world, or both are in the same instance."""
    one, other = await instance_of(session, one_id), await instance_of(session, other_id)
    return (None if one is None else one.id) == (None if other is None else other.id)


async def in_same_shared_instance(session: AsyncSession, one_id: int, other_id: int) -> bool:
    """Whether the two parties are in the same shared instance (where PvP skips the strength window)."""
    one, other = await instance_of(session, one_id), await instance_of(session, other_id)
    return one is not None and other is not None and one.id == other.id and one.mode == SHARED and one_id != other_id


async def party_ids(session: AsyncSession, instance_id: int) -> list[int]:
    """The parties inside the instance, in the order they came."""
    return list((await session.scalars(select(InstanceParty.party_id).where(InstanceParty.instance_id == instance_id).order_by(InstanceParty.id))).all())


async def party_strength(session: AsyncSession, rules: Rules, party_id: int) -> int:
    """A party's strength (the sum of its fighters' PXP), the number a shared instance matches parties by."""
    return sum(rules.pxp(fighter) for fighters in (await party_side(session, party_id, rules)).values() for fighter in fighters)


async def _closeness(session: AsyncSession, rules: Rules, instance: QuestInstance, strength: int) -> int:
    """How far the party is from the parties already there: the gap to their average strength (less is closer)."""
    there = [await party_strength(session, rules, each) for each in await party_ids(session, instance.id)]
    return abs(strength - sum(there) // len(there)) if there else 0


async def enter(
    session: AsyncSession, rules: Rules, party_id: int, quest: str, mode: str, game_map: Map, x: int, y: int,
    capacity: int = 4, join_window_seconds: int = 300,
) -> QuestInstance | None:
    """The party takes the quest and appears at tile ($x, $y) of $game_map, the quest's map. A public quest has no instance
    (None) and the party is not moved: the game puts it there as it likes. A private quest makes an instance of its own. A
    shared one puts the party into an open instance of the same quest that still has room and whose joining time has not run
    out, the one whose parties are closest to its strength, else starts one that others may join for $join_window_seconds
    (at most $capacity parties). Refused while the party is in a town, a fight, or an instance already, or when the quest is
    done in the shared instance it would join."""
    if mode not in MODES:
        raise InstanceError(f"a quest is {', '.join(MODES)}, not {mode!r}")
    if mode == PUBLIC:
        return None
    if capacity < 1 or join_window_seconds < 0 or not quest:
        raise InstanceError("an instance needs a quest, a room for at least one party and a joining time of 0 or more")
    party = await parties.get_party(session, party_id)
    if await instance_of(session, party.id) is not None:
        raise InstanceError("that party is already in an instance")
    if await towns.visit_of_party(session, party.id) is not None:
        raise InstanceError("a party in a town can't go on a quest")
    if await travel.in_fight(session, party):
        raise InstanceError("a party in a fight can't go on a quest")
    if game_map.normalize(x, y) != (x, y):
        raise InstanceError("that tile is not on the quest's map")
    instance = None
    if mode == SHARED:
        strength = await party_strength(session, rules, party.id)
        crowded = select(InstanceParty.instance_id).group_by(InstanceParty.instance_id).having(func.count() >= capacity)
        candidates = (await session.scalars(
            select(QuestInstance).where(
                QuestInstance.quest == quest, QuestInstance.mode == SHARED, QuestInstance.map_id == game_map.id,
                QuestInstance.status == OPEN, QuestInstance.joinable_until > wallclock.now(), QuestInstance.id.not_in(crowded),
            ).order_by(QuestInstance.id)
        )).all()
        if candidates:
            gaps = [(await _closeness(session, rules, each, strength), each.id, each) for each in candidates]
            instance = min(gaps, key=lambda gap: gap[:2])[2]
    if instance is None:
        instance = QuestInstance(
            quest=quest, mode=mode, map_id=game_map.id, capacity=capacity if mode == SHARED else 1,
            joinable_until=wallclock.now() + timedelta(seconds=join_window_seconds if mode == SHARED else 0),
        )
        session.add(instance)
        await session.flush()
    session.add(InstanceParty(instance_id=instance.id, party_id=party.id, from_map_id=party.map_id, from_x=party.x, from_y=party.y))
    await session.flush()
    await travel.relocate(session, party, game_map, x, y)
    return instance


async def _drop_if_empty(session: AsyncSession, instance_id: int) -> None:
    if not await session.scalar(select(func.count()).select_from(InstanceParty).where(InstanceParty.instance_id == instance_id)):
        await session.execute(delete(QuestInstance).where(QuestInstance.id == instance_id))


async def leave(session: AsyncSession, party_id: int) -> bool:
    """The party leaves its instance and stands where it was when it came in. An instance nobody is in is gone. Refused in a
    fight. Returns whether the party was in one."""
    row = await session.scalar(select(InstanceParty).where(InstanceParty.party_id == party_id))
    if row is None:
        return False
    party = await parties.get_party(session, party_id)
    if await travel.in_fight(session, party):
        raise InstanceError("a party in a fight can't leave")
    back = await session.get(Map, row.from_map_id)
    instance_id = row.instance_id
    await session.delete(row)
    await session.flush()
    await travel.relocate(session, party, back, row.from_x, row.from_y)
    await _drop_if_empty(session, instance_id)
    return True


async def complete(session: AsyncSession, party_id: int) -> list[int]:
    """The party completes its instance's quest: the first to do so wins it. Returns the other parties still in the instance (the
    game tells them the winner turned it in). Refused for a party outside an instance, and for the others once it is won."""
    instance = await instance_of(session, party_id)
    if instance is None:
        raise InstanceError("that party is not in an instance")
    if instance.status == WON:
        raise InstanceError("this quest was already completed here by another party")
    instance.status, instance.winner_party_id = WON, party_id
    await session.flush()
    return [each for each in await party_ids(session, instance.id) if each != party_id]


async def forget_party(session: AsyncSession, party_id: int) -> None:
    """A party that is gone leaves its instance (it is not moved). An instance nobody is in goes too."""
    row = await session.scalar(select(InstanceParty).where(InstanceParty.party_id == party_id))
    if row is not None:
        instance_id = row.instance_id
        await session.delete(row)
        await session.flush()
        await _drop_if_empty(session, instance_id)
