"""Storing a fight and playing its rounds, one round at a time.

``create_fight`` writes the fight as it begins. ``play_round`` takes the
commands for a round, plays it (``fights.resolve.do_combat``, from the dice of
that round's own stream), and appends it to the log with the hash of the round
before. ``load_state`` rebuilds the fight now by replaying the log, and
``verify`` checks the log: the chain of hashes, and, if asked, that every
round plays out the same again from its commands and its dice.

The hash chain starts from a hash of the initial state, so changing the
starting snapshot, any round, or removing one, breaks it. Nothing more is
needed than that, because the server is the only authority (there is nothing
to agree on, as there would be for a blockchain).
"""

import hashlib
import json
import secrets

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.base import canonical_json
from ..economy import Economy, TeamGold, credit_fight_gold
from ..heroes import inventory
from ..heroes.models import Hero
from ..relations.hooks import Change, Ref, Relations
from ..relations.service import RelationError, apply as apply_relation
from ..models import Map, World
from ..world.rng import WorldRng
from .combatant import Address, Command
from .events import Event, EventType
from .fight import Fight
from .models import FightActionRecord, FightParticipant, FightRecord
from .replay import apply_events
from .resolve import do_combat, fight_stream
from .rules import Rules
from .state import dehydrate, hydrate


class FightLogError(ValueError):
    """The log doesn't check out: ``sequence`` is the round where it first went wrong (0: the initial state)."""

    def __init__(self, sequence: int, reason: str):
        super().__init__(f"round {sequence}: {reason}" if sequence else reason)
        self.sequence = sequence
        self.reason = reason


class FightOver(RuntimeError):
    """The fight has ended and been paid out: it plays no more rounds."""


class FightNotOver(RuntimeError):
    """The fight is still being played: there is no result to save yet."""


class SequenceConflict(RuntimeError):
    """Someone else played the same round first."""


canonical = canonical_json  # the text the database stores, so a hash covers exactly what is kept


def initial_hash(initial_state: dict) -> str:
    return hashlib.sha256(canonical(initial_state).encode()).hexdigest()


def round_hash(previous: str, sequence: int, commands: list, events: list) -> str:
    return hashlib.sha256(f"{previous}:{sequence}:{canonical(commands)}:{canonical(events)}".encode()).hexdigest()


def command_record(address: Address, command: int, using: int = 0, target: Address = (0, 0, 0)) -> dict:
    """One fighter's command for a round, as it is stored."""
    return {"address": list(address), "command": int(command), "using": using, "target": list(target)}


# --- starting a fight ---------------------------------------------------------------------------------

async def create_fight(session: AsyncSession, game_map: Map, fight: Fight, x: int = 0, y: int = 0) -> FightRecord:
    """Stores a new fight on $game_map: its initial state, and who is in it."""
    record = FightRecord(guid=secrets.token_hex(16), initial_state=dehydrate(fight), map_id=game_map.id, x=x, y=y)
    session.add(record)
    await session.flush()
    for party, group, character in fight.addresses():
        fighter = fight.get((party, group, character))
        session.add(FightParticipant(
            fight_id=record.id, party=party, group_index=group, character=character, name=fighter.name,
            hero_id=fighter.charid, monster_key=fighter.monster,
        ))
    await session.flush()
    return record


async def find_by_guid(session: AsyncSession, guid: str) -> FightRecord | None:
    return await session.scalar(select(FightRecord).where(FightRecord.guid == guid))


# --- the log ---------------------------------------------------------------------------------------------

async def actions(session: AsyncSession, record: FightRecord) -> list[FightActionRecord]:
    rows = await session.scalars(select(FightActionRecord).where(FightActionRecord.fight_id == record.id).order_by(FightActionRecord.sequence))
    return list(rows.all())


def _replayed(initial_state: dict, rules: Rules, logged: list[FightActionRecord]) -> Fight:
    fight = hydrate(initial_state)
    for action in logged:
        apply_events(fight, rules, [Event.from_list(each) for each in action.events])
    return fight


async def load_state(session: AsyncSession, record: FightRecord, rules: Rules) -> tuple[Fight, int]:
    """The fight as it stands now (the initial state with every round replayed), and how many rounds have been played."""
    logged = await actions(session, record)
    return _replayed(record.initial_state, rules, logged), len(logged)


async def _stream(session: AsyncSession, record: FightRecord, sequence: int):
    game_map = await session.get(Map, record.map_id)
    world = await session.get(World, game_map.world_id)
    return fight_stream(WorldRng(world.seed), game_map.name, record.id, sequence)


def _set_commands(fight: Fight, commands: list[dict]) -> None:
    """Hands out the round's commands. Anyone not given one defends."""
    for address in fight.addresses():
        fight.get(address).command = Command.DEFEND
    for each in commands:
        fighter = fight.get(tuple(each["address"]))
        fighter.command, fighter.using, fighter.target = each["command"], each["using"], tuple(each["target"])


async def play_round(session: AsyncSession, record: FightRecord, commands: list[dict], rules: Rules, *, snapshot: bool = False) -> list[Event]:
    """Plays the next round with $commands (see ``command_record``), logs it, and returns its events.

    $snapshot also keeps the fight as it stands after the round, for ``verify`` to compare replays against.
    Raises SequenceConflict if another call played the same round first, and FightOver if the fight has ended.
    """
    logged = await actions(session, record)
    fight = _replayed(record.initial_state, rules, logged)
    if fight.over:
        raise FightOver(f"fight {record.id} is over")
    sequence = len(logged) + 1
    _set_commands(fight, commands)
    events = do_combat(fight, rules, await _stream(session, record, sequence))
    previous = logged[-1].hash if logged else initial_hash(record.initial_state)
    listed = [each.to_list() for each in events]
    row = FightActionRecord(
        fight_id=record.id, sequence=sequence, commands=commands, events=listed, previous_hash=previous,
        hash=round_hash(previous, sequence, commands, listed), final_state=dehydrate(fight) if snapshot else None,
    )
    try:
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    except IntegrityError as error:
        raise SequenceConflict(f"round {sequence} of fight {record.id} was already played") from error
    return events


# --- checking the log ---------------------------------------------------------------------------------------

async def verify(session: AsyncSession, record: FightRecord, rules: Rules, *, deep: bool = False) -> int:
    """Checks the fight's log and returns how many rounds it holds; raises FightLogError at the first problem.

    Always: the rounds are numbered 1, 2, 3, ... with none missing, and each one's hash follows from the one
    before it (and the first from the initial state). With $deep, every round is also played again from the
    state before it, its commands and its own dice, and must come out with the same events (and the same
    final state, where one was kept).
    """
    logged = await actions(session, record)
    previous = initial_hash(record.initial_state)
    for expected, action in enumerate(logged, start=1):
        if action.sequence != expected:
            raise FightLogError(expected, f"expected round {expected}, found round {action.sequence}")
        if action.previous_hash != previous:
            raise FightLogError(expected, "does not follow from the round before it")
        if round_hash(previous, action.sequence, action.commands, action.events) != action.hash:
            raise FightLogError(expected, "its commands or events were changed")
        previous = action.hash
    if deep:
        for number, action in enumerate(logged, start=1):
            fight = _replayed(record.initial_state, rules, logged[: number - 1])
            _set_commands(fight, action.commands)
            again = [each.to_list() for each in do_combat(fight, rules, await _stream(session, record, number))]
            if again != action.events:
                raise FightLogError(number, "playing it again gave different events")
            if action.final_state is not None and dehydrate(fight) != action.final_state:
                raise FightLogError(number, "playing it again gave a different fight")
    return len(logged)


# --- the result -------------------------------------------------------------------------------------------------------

async def apply_results(session: AsyncSession, record: FightRecord, rules: Rules, economy: Economy | None = None, relations: Relations | None = None) -> list[Hero]:
    """Saves a finished fight's result: to its heroes, the experience, level and stats they ended with, what their
    resources (HP, MP) stand at, which they enter their next fight with, and the
    abilities their job grants at that level; and the gold its ``Gold`` events name, paid to the teams through the
    game's $economy (``terraforma.economy``; the team's gold, DragonStar's way, if none is given); and what the fight
    did to the teams' relationships (its ``RelationChange`` events), through the game's $relations. Returns the heroes
    it updated.

    Safe to call again: the heroes get what the fight says rather than more of it, and the gold is paid and the
    relationships moved once (the fight records that they have been). Raises FightNotOver if the fight is still going.
    """
    fight, _played = await load_state(session, record, rules)
    if not fight.over:
        raise FightNotOver(f"fight {record.id} is not over")
    updated = []
    for address in fight.addresses():
        fighter = fight.get(address)
        hero = await session.get(Hero, fighter.charid) if fighter.charid is not None else None
        if hero is None:  # a monster, or a hero deleted since the fight began
            continue
        hero.xp, hero.level, hero.stats = fighter.exp, fighter.level, dict(fighter.base)
        hero.vitals = {name: fighter.current[name] for name in rules.resource_names if name in fighter.current}
        await session.flush()
        await inventory.grant_abilities(session, hero)
        updated.append(hero)
    await credit_fight_gold(session, economy or TeamGold(), record, await gold_payments(session, record))
    await save_drops(session, record, fight)
    await save_relations(session, record, relations or Relations())
    return updated


async def save_relations(session: AsyncSession, record: FightRecord, relations: Relations) -> bool:
    """Applies what the fight did to the teams' relationships (its ``RelationChange`` events, a team's changes to its view
    of another added up), once (True if it did now). A change the game's rules refuse, or for a team that has gone, is
    dropped."""
    claimed = await session.execute(update(FightRecord).where(FightRecord.id == record.id, FightRecord.relations_applied.is_(False)).values(relations_applied=True))
    if claimed.rowcount == 0:
        return False
    moved: dict[tuple[int, int], int] = {}
    for action in await actions(session, record):
        for raw in action.events:
            each = Event.from_list(raw)
            if each.type is EventType.RELATION_CHANGE:
                team, other, delta = each.data
                moved[(team, other)] = moved.get((team, other), 0) + delta
    for (team, other), delta in sorted(moved.items()):
        if delta:
            try:
                async with session.begin_nested():
                    await apply_relation(session, relations, Change(Ref("team", team), Ref("team", other), delta=delta, by="game", reason="fight"))
            except RelationError:
                continue
    await session.refresh(record, ["relations_applied"])
    return True


async def save_drops(session: AsyncSession, record: FightRecord, fight: Fight) -> bool:
    """Puts what the fight dropped in the heroes' inventories, once (True if it did now). The log's ``Drop`` events say
    who got what; ``DropLost`` ones did not fit and are not saved."""
    claimed = await session.execute(update(FightRecord).where(FightRecord.id == record.id, FightRecord.drops_saved.is_(False)).values(drops_saved=True))
    if claimed.rowcount == 0:
        return False
    for action in await actions(session, record):
        for raw in action.events:
            each = Event.from_list(raw)
            if each.type is EventType.DROP:
                hero_id = fight.get(tuple(each.data[:3])).charid
                hero = await session.get(Hero, hero_id) if hero_id is not None else None
                if hero is not None:
                    await inventory.add_item(session, hero, each.data[3], each.data[4])
    await session.refresh(record, ["drops_saved"])
    return True


async def gold_payments(session: AsyncSession, record: FightRecord) -> list[tuple[int, int]]:
    """What the fight's ``Gold`` events pay, as ``(team id, amount)``, a team's shares added up."""
    paid: dict[int, int] = {}
    for action in await actions(session, record):
        for raw in action.events:
            each = Event.from_list(raw)
            if each.type is EventType.GOLD:
                _party, team, amount = each.data
                paid[team] = paid.get(team, 0) + amount
    return sorted(paid.items())
