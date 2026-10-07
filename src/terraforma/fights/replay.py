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
from .state import hydrate_party
from .status import age, count_turn, place, take_off


def apply_events(fight: Fight, rules: Rules, events: list[Event]) -> None:
    actor: Combatant | None = None
    for each in events:
        data = each.data
        kind = each.type
        if kind is EventType.TURN:
            actor = fight.get(tuple(data[:3]))
            count_turn(actor)
        elif kind is EventType.DAMAGE:
            fight.get(tuple(data[:3])).current[rules.vital] -= data[3]
        elif kind is EventType.RESTORE or kind is EventType.ALTER_STAT:
            fight.get(tuple(data[:3])).current[data[3]] += data[4]
        elif kind is EventType.DIED:
            fight.get(tuple(data[:3])).current[rules.vital] = 0
        elif kind is EventType.FLED:
            fight.get(tuple(data[:3])).fled = True
        elif kind is EventType.FIGHT_OVER:
            fight.over = True
        elif kind in (EventType.DROP,):
            fight.get(tuple(data[:3])).add_item(rules, fight.drop_item(data[3]), data[4])
        elif kind is EventType.XP_DEBT:
            fight.get(tuple(data[:3])).xp_debts.append(list(data[3:]))
        elif kind is EventType.XP_EARNED:
            fight.get(tuple(data[:3])).exp += data[3]
        elif kind is EventType.LEVEL_UP:
            fighter = fight.get(tuple(data[:3]))
            fighter.level = data[3]
            for stat, gain in data[4].items():
                fighter.base[stat] += gain
        elif kind in (EventType.USE_ITEM, EventType.EXPEND_AMMO):
            actor.remove_item(data[0], 1)
        elif kind is EventType.EQUIP_SLOT:
            actor.equipment[data[1]] = data[0]
        elif kind is EventType.UNEQUIP_SLOT:
            actor.equipment[data[0]] = None
        elif kind is EventType.STATUS_APPLIED:
            place(fight.get(tuple(data[:3])), fight.statuses[data[3]], tuple(data[4:7]), data[7])
        elif kind is EventType.STATUS_REMOVED:
            take_off(fight.get(tuple(data[:3])), data[3], tuple(data[4:7]))
        elif kind is EventType.PARTY_JOINED:
            fight.parties[data[0]] = hydrate_party(data[1], fight.statuses)
            for party, (allies, enemies) in data[2].items():  # (a stored event's keys are text)
                fight.parties[int(party)].allies, fight.parties[int(party)].enemies = set(allies), set(enemies)
        elif kind is EventType.ROUND_END:
            for address in fight.addresses():
                age(fight.get(address))
