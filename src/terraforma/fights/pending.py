"""Pending drops: drops the game held for the party's players to settle (``Rules.drop_mode``).

Two ways, each its own call. A *need/want* drop waits until every hero of the winning party that fought has said
``need``, ``want`` or ``pass``; then each hero who needed (or, if nobody needed, wanted) rolls 1 to 100 from the fight's
own stream (``"drop", number`` under the fight's), the highest roll wins and a tie goes to the lower hero id. If everybody
passes it is ``unclaimed`` (what happens to it then is the game's). An *assign* drop is given by one hero to another
when the game's rules say that hero may (``Rules.may_assign_drop``: the engine has no party leader, so the default is
nobody).

Service functions: they raise ``PendingError`` (a message the player can read) and change nothing when they do, as long
as the caller's transaction rolls back (the calls' session does).
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes import inventory
from ..heroes.models import Hero
from ..models import Map, World
from ..world.rng import WorldRng
from .drops import ASSIGN, NEED_WANT
from .events import Event, EventType
from .models import FightParticipant, FightRecord, PendingDrop, PendingDropChoice
from .rules import Rules

CHOICES = ("need", "want", "pass")
OPEN, AWARDED, UNCLAIMED = "open", "awarded", "unclaimed"


class PendingError(ValueError):
    """Something the player can fix: the message says what."""


async def hold(session: AsyncSession, record: FightRecord, raw_events: list[list]) -> int:
    """Makes a pending drop of each ``DropHeld`` event in the fight's log that has none yet. Returns how many it made."""
    held = [Event.from_list(raw) for raw in raw_events]
    held = [each for each in held if each.type is EventType.DROP_HELD]
    known = set(await session.scalars(select(PendingDrop.number).where(PendingDrop.fight_id == record.id)))
    made = 0
    for number, each in enumerate(held):
        if number not in known:
            party, item, qty, mode = each.data
            session.add(PendingDrop(fight_id=record.id, number=number, party=party, item_key=item, qty=qty, mode=mode,
                                    map_id=record.map_id, x=record.x, y=record.y))
            made += 1
    await session.flush()
    return made


async def heroes_of(session: AsyncSession, pending: PendingDrop) -> list[int]:
    """The heroes who fought in the pending drop's party, by id: the ones it is for."""
    rows = await session.scalars(select(FightParticipant.hero_id).where(
        FightParticipant.fight_id == pending.fight_id, FightParticipant.party == pending.party,
        FightParticipant.hero_id.is_not(None)))
    return sorted(set(rows.all()))


async def for_hero(session: AsyncSession, hero: Hero, *, only_open: bool = True) -> list[PendingDrop]:
    """The pending drops of the fights the hero fought in, for the party it fought in, oldest first."""
    query = select(PendingDrop).join(FightParticipant, (FightParticipant.fight_id == PendingDrop.fight_id) & (FightParticipant.party == PendingDrop.party)).where(FightParticipant.hero_id == hero.id)
    if only_open:
        query = query.where(PendingDrop.status == OPEN)
    return list((await session.scalars(query.order_by(PendingDrop.id))).all())


async def _locked(session: AsyncSession, hero: Hero, pending_id: int) -> tuple[PendingDrop, list[int]]:
    """The pending drop (row locked, so two calls cannot settle it twice) and its heroes, if $hero is one of them."""
    pending = await session.scalar(select(PendingDrop).where(PendingDrop.id == pending_id).with_for_update())
    heroes = await heroes_of(session, pending) if pending is not None else []
    if pending is None or hero.id not in heroes:
        raise PendingError("there's no such drop for this hero")  # the same whether it is missing or not theirs
    if pending.status != OPEN:
        raise PendingError("that drop has been settled")
    return pending, heroes


async def _award(session: AsyncSession, pending: PendingDrop, winner: Hero) -> None:
    pending.status, pending.winner_id = AWARDED, winner.id
    async with session.begin_nested():
        pending.lost = await inventory.add_item(session, winner, pending.item_key, pending.qty)
    await session.flush()


async def choose(session: AsyncSession, hero: Hero, pending_id: int, choice: str) -> PendingDrop:
    """The hero says ``need``, ``want`` or ``pass`` on a need/want drop (and may change their mind until it is settled).
    The last hero to answer settles it."""
    pending, heroes = await _locked(session, hero, pending_id)
    if pending.mode != NEED_WANT:
        raise PendingError("that drop is not rolled for")
    if choice not in CHOICES:
        raise PendingError("say need, want or pass")
    mine = await session.scalar(select(PendingDropChoice).where(PendingDropChoice.pending_id == pending.id, PendingDropChoice.hero_id == hero.id))
    if mine is None:
        session.add(PendingDropChoice(pending_id=pending.id, hero_id=hero.id, choice=choice))
    else:
        mine.choice = choice
    await session.flush()
    answers = list((await session.scalars(select(PendingDropChoice).where(PendingDropChoice.pending_id == pending.id))).all())
    if {each.hero_id for each in answers} >= set(heroes):
        await _settle_rolls(session, pending, answers)
    return pending


async def _settle_rolls(session: AsyncSession, pending: PendingDrop, answers: list[PendingDropChoice]) -> None:
    rolling = [each for each in answers if each.choice == "need"] or [each for each in answers if each.choice == "want"]
    if not rolling:
        pending.status = UNCLAIMED
        await session.flush()
        return
    record = await session.get(FightRecord, pending.fight_id)
    game_map = await session.get(Map, record.map_id)
    world = await session.get(World, game_map.world_id)
    # its own stream under the fight's: a roll never moves the dice of the fight itself
    rng = WorldRng(world.seed).stream("map", game_map.name, "fight", record.id, "drop", pending.number)
    rolling.sort(key=lambda each: each.hero_id)
    for each in rolling:
        each.roll = rng.randint(1, 100)
    best = max(rolling, key=lambda each: (each.roll, -each.hero_id))
    await _award(session, pending, await session.get(Hero, best.hero_id))


async def assign(session: AsyncSession, rules: Rules, hero: Hero, pending_id: int, to_hero_id: int) -> PendingDrop:
    """The hero gives an ``assign`` drop to another hero of the party, if the game's rules let them."""
    pending, heroes = await _locked(session, hero, pending_id)
    if pending.mode != ASSIGN:
        raise PendingError("that drop is not handed out by anyone")
    if not rules.may_assign_drop(heroes, hero.id):
        raise PendingError("you can't hand this drop out")
    if to_hero_id not in heroes:
        raise PendingError("choose one of the party's heroes")
    await _award(session, pending, await session.get(Hero, to_hero_id))
    return pending


async def view(session: AsyncSession, pending: PendingDrop, hero: Hero) -> dict:
    """What a hero of the party sees of a pending drop: what it is, how it is settled, their own answer, and (once it is
    settled) who won. The others' answers stay hidden until then, so nobody is swayed by them."""
    answers = list((await session.scalars(select(PendingDropChoice).where(PendingDropChoice.pending_id == pending.id))).all())
    mine = next((each for each in answers if each.hero_id == hero.id), None)
    shown = {"id": pending.id, "fight_id": pending.fight_id, "item": pending.item_key, "qty": pending.qty, "mode": pending.mode,
             "status": pending.status, "your_choice": mine.choice if mine else None, "winner_id": pending.winner_id, "lost": pending.lost}
    if pending.status != OPEN:
        shown["rolls"] = [{"hero_id": each.hero_id, "choice": each.choice, "roll": each.roll} for each in sorted(answers, key=lambda each: each.hero_id)]
    return shown
