"""Rebuilding a fight's state from its events.

Applying a round's events, in order, to the fight as it was before the round
leaves it as the round left it: HP and MP, spent items and ammunition, gear
changed. The events carry everything needed (no dice are rolled), which is
what lets a stored fight be replayed and shown to spectators.
"""

from .combatant import Combatant
from .events import Event, EventType
from .fight import Fight
from .rules import Rules


def apply_events(fight: Fight, rules: Rules, events: list[Event]) -> None:
    actor: Combatant | None = None
    for each in events:
        data = each.data
        kind = each.type
        if kind is EventType.TURN:
            actor = fight.get(tuple(data[:3]))
        elif kind is EventType.DAMAGE:
            fight.get(tuple(data[:3])).current[rules.vital] -= data[3]
        elif kind is EventType.RESTORE or kind is EventType.ALTER_STAT:
            fight.get(tuple(data[:3])).current[data[3]] += data[4]
        elif kind is EventType.DIED:
            fight.get(tuple(data[:3])).current[rules.vital] = 0
        elif kind is EventType.XP_DEBT:
            fight.get(tuple(data[:3])).xp_debts.append(list(data[3:]))
        elif kind is EventType.XP_EARNED:
            fight.get(tuple(data[:3])).exp += data[3]
        elif kind is EventType.LEVEL_UP:
            fighter = fight.get(tuple(data[:3]))
            fighter.level = data[3]
            for stat, gain in data[4].items():
                fighter.base[stat] += gain
            fighter.reset_stats(rules)
        elif kind in (EventType.USE_ITEM, EventType.EXPEND_AMMO):
            actor.remove_item(data[0], 1)
        elif kind is EventType.EQUIP_SLOT:
            actor.equipment[data[1]] = data[0]
        elif kind is EventType.UNEQUIP_SLOT:
            actor.equipment[data[0]] = None
