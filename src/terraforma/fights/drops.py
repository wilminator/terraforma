"""Item drops: what a fight's monsters leave behind, and who gets it.

Pure code, no database or web. A *drop table* is content (``drop_tables.json``): a list of entries, each an item, a
chance (out of ``Rules.drop_chance_scale``, 10000 by default, so a very rare drop can be 1 in 10000 and a sure one
10000) and how many; or, for a *weighted* table, a few ``rolls`` each picking one entry by its weight (an entry with
no item means nothing drops). A monster names the tables it rolls when it dies (``drops``), and a fight may carry
*area* tables of its own (the map's, once there are maps).

``settle`` runs when a fight ends, after the experience and the gold: for each party of players that won, it rolls the
tables of the monsters that died, and the area tables (once per fight, unless ``Rules.map_drops_once_per_fight`` is
off), from the fight's own stream, so it replays. Who receives each item is the game's rule
(``Rules.drop_recipients``). What lands is a ``Drop`` event and the item is added to the hero's inventory in the
fight; what does not fit is a ``DropLost`` event, never lost silently.
"""

import random
from collections.abc import Sequence
from dataclasses import dataclass

from .combatant import Address
from .events import Event, EventType, event
from .specs import ItemSpec

ONE = "one"
EACH_TEAM = "each_team"
EACH_MEMBER = "each_member"
SHARES = (ONE, EACH_TEAM, EACH_MEMBER)


@dataclass(frozen=True)
class DropEntry:
    #: What drops (None: nothing, which only a weighted table uses).
    item: ItemSpec | None
    #: In a plain table, the chance out of ``Rules.drop_chance_scale``; in a weighted one, the weight.
    chance: int = 0
    weight: int = 0
    minimum: int = 1
    maximum: int = 1
    #: Who gets it: one person, one in each team of the winning side, or every hero of the winning party.
    share: str = ONE


@dataclass(frozen=True)
class DropTable:
    key: str
    name: str = ""
    weighted: bool = False
    #: A weighted table: how many times it picks.
    rolls: int = 1
    entries: tuple[DropEntry, ...] = ()


@dataclass(frozen=True)
class Roll:
    """One entry that came up."""

    item: ItemSpec
    quantity: int
    share: str


def _quantity(entry: DropEntry, rng: random.Random) -> int:
    return entry.minimum if entry.minimum == entry.maximum else rng.randint(entry.minimum, entry.maximum)


def roll_table(scale: int, table: DropTable, rng: random.Random) -> list[Roll]:
    """What a table drops this once. A plain table rolls every entry on its own; a weighted one picks ``rolls`` times."""
    found: list[Roll] = []
    if table.weighted:
        total = sum(entry.weight for entry in table.entries)
        for _ in range(table.rolls if total else 0):
            pick = rng.randint(1, total)
            for entry in table.entries:
                pick -= entry.weight
                if pick <= 0:
                    if entry.item is not None:
                        found.append(Roll(entry.item, _quantity(entry, rng), entry.share))
                    break
        return found
    for entry in table.entries:
        if entry.item is not None and rng.randint(1, scale) <= entry.chance:
            found.append(Roll(entry.item, _quantity(entry, rng), entry.share))
    return found


def contributors(fight, monsters: Sequence[Address]) -> set[Address]:
    """Who helped bring the monsters down, as the experience debts the fight keeps tell it: whoever harmed one (a hit, or
    through a status, or by placing a bad status on it), and whoever buffed one of those. Healing does not count (healers
    are paid experience directly); everyone counts the same, whatever the size of their hit."""
    harmers = {tuple(debt[:3]) for monster in monsters for debt in fight.get(monster).xp_debts if debt[3] > 0}
    pool = set(harmers)
    for address in harmers:
        if address in fight.addresses():
            pool.update(fight.get(address).buffed_by)
    return pool


def default_recipients(fight, party: int, monsters: Sequence[Address], share: str, rng: random.Random) -> list[Address]:
    """The engine's way: one at random among the heroes who contributed (all the party's heroes if none did); one per
    team the same way; or every hero of the party."""
    heroes = [address for address in fight.addresses() if address[0] == party and fight.get(address).charid is not None]
    if not heroes:
        return []
    if share == EACH_MEMBER:
        return heroes
    pool = [address for address in sorted(contributors(fight, monsters)) if address in heroes] or heroes
    if share == ONE:
        return [rng.choice(pool)]
    chosen = []
    for _team, members in sorted(fight.parties[party].teams.items()):
        on_team = [address for address in heroes if fight.get(address).charid in members]
        among = [address for address in pool if address in on_team] or on_team
        if among:
            chosen.append(rng.choice(among))
    return chosen


def settle(rules, fight, rng: random.Random) -> list[Event]:
    """Rolls the drops of a finished fight for each party of players that won. Returns its events, already applied."""
    events: list[Event] = []
    for index, party in fight.parties.items():
        if not party.teams or party.dead(rules):
            continue
        _, enemies = rules.alignment(fight, index)
        dead = [address for address in fight.addresses() if address[0] in enemies and not fight.get(address).alive(rules)]
        rolls: list[tuple[list[Address], Roll]] = []
        area = [fight.drop_tables[key] for key in fight.area_drops if key in fight.drop_tables]
        for monster in dead:
            for key in fight.get(monster).drops:
                if key in fight.drop_tables:
                    rolls.extend(([monster], roll) for roll in roll_table(rules.drop_chance_scale, fight.drop_tables[key], rng))
            if area and not rules.map_drops_once_per_fight:
                for table in area:
                    rolls.extend(([monster], roll) for roll in roll_table(rules.drop_chance_scale, table, rng))
        if area and rules.map_drops_once_per_fight and dead:
            for table in area:
                rolls.extend((dead, roll) for roll in roll_table(rules.drop_chance_scale, table, rng))
        for monsters, roll in rolls:
            for address in rules.drop_recipients(fight, index, monsters, roll.share, rng):
                events.extend(give(rules, fight, address, roll.item, roll.quantity))
    return events


def give(rules, fight, address: Address, item: ItemSpec, quantity: int) -> list[Event]:
    """Puts the item in the fighter's inventory as far as it fits, and says what landed and what did not."""
    left = fight.get(address).add_item(rules, item, quantity)
    events = []
    if quantity - left:
        events.append(event(EventType.DROP, *address, item.key, quantity - left))
    if left:
        events.append(event(EventType.DROP_LOST, *address, item.key, left))
    return events
