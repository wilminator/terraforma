"""Pure rules about gear that heroes (heroes.inventory) and fights (fights.combatant) share. No database."""

import math
from dataclasses import dataclass
from enum import Enum

SIDED = ("hand", "ammo", "arm")
AMMO_SLOTS = ("ammo", "lammo", "rammo")


class EquipOutcome(Enum):
    NOT_FOUND = "not_found"  # no stack at that position
    NOT_EQUIPABLE = "not_equipable"  # the item takes no slot
    NEEDS_UNEQUIPPING = "needs_unequipping"  # something is in the way: take occupying_position off first
    INCOMPATIBLE_AMMO = "incompatible_ammo"  # ammo with no weapon that takes it
    SUCCESS = "success"  # equipped in .slots


@dataclass(frozen=True)
class EquipResult:
    outcome: EquipOutcome
    occupying_position: int | None = None
    slots: tuple[str, ...] = ()


def find_slot(slot: str, side: int) -> str:
    """The concrete slot for $slot: a sided slot becomes "l" or "r" plus its name (side 0 is left)."""
    return ("l" if side == 0 else "r") + slot if slot in SIDED else slot


def round_half_up(value: float) -> int:
    """Halves round away from zero, as DragonStar's (PHP's) rounding does; Python's round() goes to even."""
    return int(math.copysign(math.floor(abs(value) + 0.5), value))


def equipment_bonus(items: list, stat: str, base: int) -> int:
    """$base for $stat with the worn $items' bonuses: all the percentages first, then the flat amounts."""
    percent = sum(item.stat_percent.get(stat, 0) for item in items)
    value = base * (percent / 100.0 + 1.0)
    value += sum(item.stat_bonus.get(stat, 0) for item in items)
    return round_half_up(value)
