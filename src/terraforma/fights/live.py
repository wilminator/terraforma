"""Live fights: starting one, taking the players' commands, playing rounds on time, and saving the result.

A fight waits for its round. Each player commands the fighters of their own heroes (``submit_command``); the round
plays as soon as every player's living fighter has committed, or when ``Rules.round_seconds`` run out
(``resolve_overdue``, which the server's timer calls), and a fighter that has not committed defends. Monsters choose
with the AI (``fights.ai``) from a stream of their own under the round's, and what they chose is stored with the round
like any other command, so a replay never runs the AI again. When a round ends the fight, its result is saved to the
heroes and its gold is paid through the game's economy (``store.apply_results``).

Time here is the wall clock (``wallclock.now()``): players are waiting. Everything else is the fight engine's, so the
same stored fight replays exactly whatever the clock said.

A fight is started by the server (``start_team_fight``: the map's encounters will call it), not by a player's call, so
nobody picks their own opponents.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .. import wallclock
from ..economy import Economy
from ..relations.hooks import Relations
from ..heroes.models import Hero, Team
from ..models import Map, World
from ..world.rng import WorldRng
from . import ai, store
from .build import known_drop_tables, known_statuses, monster_fighter, team_party
from .combatant import Address
from .events import Event
from .fight import build_fight
from .models import FightCommandRecord, FightParticipant, FightRecord
from .resolve import valid_action
from .rules import Rules


class FightError(ValueError):
    """Something the caller can fix: the message says what."""


class NotFound(FightError):
    pass


class NotYours(FightError):
    """The fighter is not one of this account's."""


class Refused(FightError):
    """The fight can't do that now."""


@dataclass
class RoundResult:
    round: int
    events: list[Event]
    over: bool
    deadline: datetime | None

    def message(self, fight_id: int) -> dict:
        """What the fight's watchers are sent."""
        return {
            "type": "round", "fight": fight_id, "round": self.round, "over": self.over,
            "deadline": aware(self.deadline).isoformat() if self.deadline else None,
            "events": [each.to_list() for each in self.events],
        }


def aware(moment: datetime | None) -> datetime | None:
    """A time read back from the database with its zone (some databases drop it: it is always UTC)."""
    return moment if moment is None or moment.tzinfo is not None else moment.replace(tzinfo=UTC)


# --- starting ---------------------------------------------------------------------------------------------------

def _groups(fighters: list, size: int) -> dict[int, list]:
    return {number: fighters[start:start + size] for number, start in enumerate(range(0, len(fighters), size))}


async def start_team_fight(session: AsyncSession, team: Team, monster_keys: list[str], rules: Rules, area_drops: list[str] | None = None) -> FightRecord:
    """A fight between a player's team (party 0) and monsters (party 1), where the team's first hero stands. The round
    clock starts now. $area_drops are the keys of the area's own drop tables (the map's), rolled when the team wins. Raises Refused for a team with no heroes, no monsters, too many of them, or a hero who is in a
    fight already."""
    heroes, teams = await team_party(session, team)
    if not heroes:
        raise Refused("that team has no heroes")
    if not 1 <= len(monster_keys) <= rules.party_size:
        raise Refused(f"a fight needs between 1 and {rules.party_size} monsters")
    busy = await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_([hero for members in teams.values() for hero in members]), FightRecord.finished.is_(False)).limit(1)
    )
    if busy is not None:
        raise Refused("a hero of that team is already in a fight")
    monsters = [await monster_fighter(session, key, rules) for key in monster_keys]
    wanted = {key for monster in monsters for key in monster.drops} | set(area_drops or ())
    tables = await known_drop_tables(session, wanted)
    if missing := set(area_drops or ()) - set(tables):
        raise Refused(f"there is no drop table {sorted(missing)[0]!r}")
    fight = build_fight({0: _groups(heroes, rules.group_size), 1: _groups(monsters, rules.group_size)}, await known_statuses(session), tables, list(area_drops or ()))
    fight.parties[0].teams = teams
    first = await session.get(Hero, heroes[0].charid)
    record = await store.create_fight(session, await session.get(Map, first.map_id), fight, first.x, first.y)
    record.round_deadline = wallclock.now() + timedelta(seconds=rules.round_seconds)
    await session.flush()
    return record


# --- who commands whom ------------------------------------------------------------------------------------------

async def owners(session: AsyncSession, record: FightRecord) -> dict[Address, int]:
    """The account that plays each fighter that is a hero (monsters have no entry: the AI plays them)."""
    rows = await session.execute(
        select(FightParticipant.party, FightParticipant.group_index, FightParticipant.character, Hero.account_id)
        .join(Hero, Hero.id == FightParticipant.hero_id).where(FightParticipant.fight_id == record.id)
    )
    return {(party, group, character): account_id for party, group, character, account_id in rows.all()}


async def get_record(session: AsyncSession, fight_id: int) -> FightRecord:
    record = await session.get(FightRecord, fight_id)
    if record is None:
        raise NotFound("there's no such fight")
    return record


async def waiting(session: AsyncSession, record: FightRecord, round_number: int) -> dict[Address, FightCommandRecord]:
    rows = await session.scalars(select(FightCommandRecord).where(FightCommandRecord.fight_id == record.id, FightCommandRecord.round_number == round_number))
    return {(row.party, row.group_index, row.character): row for row in rows.all()}


async def submit_command(session: AsyncSession, record: FightRecord, account_id: int, address: Address, command: int, using: int,
                         target: Address, rules: Rules) -> int:
    """Takes (or replaces) the command of the fighter at $address, which must be one of $account_id's living heroes, for
    the round being waited for, and returns that round's number. Raises NotYours, Refused (the fight is over, the fighter
    is down, or it can't do that) or NotFound."""
    if record.finished:
        raise Refused("that fight is over")
    fight, played = await store.load_state(session, record, rules)
    mine = await owners(session, record)
    if address not in fight.addresses():
        raise NotFound("there's no such fighter")
    if mine.get(address) != account_id:
        raise NotYours("that fighter isn't yours")
    fighter = fight.get(address)
    if not fighter.alive(rules):
        raise Refused("that fighter is down")
    fighter.command, fighter.using, fighter.target = command, using, target
    if not valid_action(fighter):
        raise Refused("that fighter can't do that")
    number = played + 1
    row = (await waiting(session, record, number)).get(address)
    if row is None:
        session.add(FightCommandRecord(fight_id=record.id, round_number=number, party=address[0], group_index=address[1], character=address[2],
                                       command=int(command), using_index=using, target=list(target)))
    else:
        row.command, row.using_index, row.target = int(command), using, list(target)
    await session.flush()
    return number


async def everyone_committed(session: AsyncSession, record: FightRecord, rules: Rules) -> bool:
    """Whether every living fighter that a player commands has a command waiting for this round."""
    fight, played = await store.load_state(session, record, rules)
    mine, ready = await owners(session, record), await waiting(session, record, played + 1)
    return all(address in ready for address in mine if address in fight.addresses() and fight.get(address).alive(rules))


# --- playing a round ---------------------------------------------------------------------------------------------

async def _ai_stream(session: AsyncSession, record: FightRecord, round_number: int):
    game_map = await session.get(Map, record.map_id)
    world = await session.get(World, game_map.world_id)
    return WorldRng(world.seed).stream("map", game_map.name, "fight", record.id, "round", round_number, "ai")


async def resolve_round(session: AsyncSession, record: FightRecord, rules: Rules, economy: Economy | None = None, relations: Relations | None = None) -> RoundResult:
    """Plays the round being waited for with what has been committed: the players' commands, the AI's for the monsters,
    and a defend for any player's fighter that has not committed. Then, if that ended the fight, saves its result;
    if not, starts the next round's clock. Raises Refused for a finished fight and store.SequenceConflict if someone
    played the round first."""
    if record.finished:
        raise Refused("that fight is over")
    fight, played = await store.load_state(session, record, rules)
    number = played + 1
    mine, ready = await owners(session, record), await waiting(session, record, number)
    rng = await _ai_stream(session, record, number)
    commands = []
    for address in fight.addresses():
        fighter = fight.get(address)
        if not fighter.alive(rules):
            continue
        if address in mine:
            if address in ready:
                row = ready[address]
                commands.append(store.command_record(address, row.command, row.using_index, tuple(row.target)))
        else:
            ai.commit(fighter, ai.choose_command(rules, fight, address, rng))
            commands.append(store.command_record(address, fighter.command, fighter.using, fighter.target))
    try:
        events = await store.play_round(session, record, commands, rules)
    except store.FightOver as error:
        record.finished, record.round_deadline = True, None
        raise Refused("that fight is over") from error
    await session.execute(delete(FightCommandRecord).where(FightCommandRecord.fight_id == record.id, FightCommandRecord.round_number == number))
    now_fight, _played = await store.load_state(session, record, rules)
    if now_fight.over:
        record.finished, record.round_deadline = True, None
        await store.apply_results(session, record, rules, economy, relations)
    else:
        record.round_deadline = wallclock.now() + timedelta(seconds=rules.round_seconds)
    await session.flush()
    return RoundResult(number, events, now_fight.over, record.round_deadline)


async def due(session: AsyncSession, now: datetime | None = None) -> list[int]:
    """The fights whose round time has run out."""
    rows = await session.scalars(
        select(FightRecord.id).where(FightRecord.finished.is_(False), FightRecord.round_deadline <= (now or wallclock.now())).order_by(FightRecord.round_deadline)
    )
    return list(rows.all())


async def resolve_overdue(sessionmaker: async_sessionmaker, rules: Rules, economy: Economy | None, channels, relations: Relations | None = None) -> int:
    """Plays every round whose time has run out, tells the fights' watchers, and returns how many it played. Each fight
    is its own transaction, so one that fails does not hold up the rest; a round someone else just played is skipped."""
    async with sessionmaker() as session:
        ids = await due(session)
    played = 0
    for fight_id in ids:
        try:
            async with sessionmaker() as session, session.begin():
                record = await session.get(FightRecord, fight_id)
                if record.finished or record.round_deadline is None or aware(record.round_deadline) > wallclock.now():
                    continue  # played meanwhile
                result = await resolve_round(session, record, rules, economy, relations)
        except (store.SequenceConflict, Refused):
            continue
        played += 1
        await channels.push(fight_id, result.message(fight_id))
    return played


# --- what a page sees ------------------------------------------------------------------------------------------------

async def view(session: AsyncSession, record: FightRecord, rules: Rules, account_id: int | None = None) -> dict:
    """The fight as a page shows it: whose turn to be commanded, who is standing and with how much, which fighters are
    the caller's and have committed, and when the round plays."""
    fight, played = await store.load_state(session, record, rules)
    mine, ready = await owners(session, record), await waiting(session, record, played + 1)
    return {
        "id": record.id, "guid": record.guid, "round": played + 1, "over": fight.over or record.finished,
        "deadline": aware(record.round_deadline).isoformat() if record.round_deadline else None,
        "fighters": [
            {
                "party": address[0], "group": address[1], "character": address[2], "name": fight.get(address).name,
                "alive": fight.get(address).alive(rules),
                "resources": {name: [fight.get(address).current[name], fight.get(address).get_base(rules, name)] for name in rules.resource_names},
                "statuses": [token.spec.key for token in fight.get(address).tokens],
                "yours": account_id is not None and mine.get(address) == account_id,
                "committed": address in ready,
            }
            for address in fight.addresses()
        ],
    }


async def watchers(session: AsyncSession, record: FightRecord) -> set[int]:
    """The accounts with a hero in the fight."""
    return set((await owners(session, record)).values())

