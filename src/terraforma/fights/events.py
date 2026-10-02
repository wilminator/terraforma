"""What happens in a fight, as an ordered list of events.

An event is a type and its data, the same messages DragonStar sends to
the browser ("Turn", "Damage", "Died", ...). A round's events say
everything that happened in it, and the fight's state after the round is
what you get by applying them in order (fights.replay), so a fight can be
stored as its events, replayed, and shown to spectators.

Events are plain lists in JSON, ``[type, [data...]]``, and read back the same.
"""

from dataclasses import dataclass
from enum import StrEnum


class EventType(StrEnum):
    TURN = "Turn"  # [party, group, character]: whose turn it is
    TARGET = "Target"  # [party, group, character, scope]
    ATTACK = "Attack"  # [item key or None, "left"|"right", range]
    SKILL = "Skill"  # [ability key]
    SPELL = "Spell"  # [ability key]
    ITEM = "Item"  # [item key]
    EQUIP = "Equip"  # [item key] or [item key, ammo key]
    UNEQUIP = "Unequip"  # [item name]
    DEFEND = "Defend"  # []
    RUN = "Run"  # []
    NO_MP = "NoMP"  # [party, group, character]
    NO_AMMO = "NoAmmo"  # [party, group, character]
    MISS = "Miss"  # [party, group, character]
    NO_EFFECT = "NoEffect"  # [party, group, character]
    DAMAGE = "Damage"  # [party, group, character, amount, critical]
    RESTORE = "Restore"  # [party, group, character, resource, amount]
    REVIVED = "Revived"  # [party, group, character]
    DIED = "Died"  # [party, group, character, amount, overkill]
    ALTER_STAT = "AlterStat"  # [party, group, character, stat, amount]
    USE_ITEM = "UseItem"  # [inventory index]: one of a stack used up
    EXPEND_AMMO = "ExpendAmmo"  # [inventory index]
    EQUIP_SLOT = "EquipSlot"  # [inventory index, slot]
    UNEQUIP_SLOT = "UnequipSlot"  # [slot]


@dataclass(frozen=True)
class Event:
    type: EventType
    data: tuple = ()

    def to_list(self) -> list:
        return [self.type.value, list(self.data)]

    @classmethod
    def from_list(cls, raw: list) -> "Event":
        return cls(EventType(raw[0]), tuple(raw[1]))


def event(kind: EventType, *data) -> Event:
    return Event(kind, tuple(data))
