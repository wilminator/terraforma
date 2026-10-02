"""Resolving a round: who goes in what order, and what each action does.

This is DragonStar's process, kept as it is: every fighter has already chosen
a command (``command``, ``using``, ``target``); ``do_combat`` orders them,
lets each act, and returns the events that tell what happened. It changes
the fight as it goes; applying the same events to a copy of the fight as it
was (``replay.apply_events``) gives the same result.

Pure: no database, no web, no ``random`` module. The only randomness is the
``random.Random`` it is handed, in a fixed order, so the same fight with the
same stream plays out the same way every time. Use ``fight_stream`` to get
that stream.
"""

import math
import random

from ..world.rng import WorldRng
from . import specs
from .combatant import Address, Combatant, Command
from .events import Event, EventType, event
from .fight import Fight
from .gear import EquipOutcome
from .rules import Rules
from .specs import EffectSpec
from .targets import expand, lost_target


def fight_stream(world: WorldRng, map_name: str, fight_id: int, round_number: int | None = None) -> random.Random:
    """The random stream for one fight (or, with $round_number, one round of it): its own, under its map's, so the
    fight replays exactly whatever else happens in the world. A round has its own stream so it can be played
    again from the fight's state at the start of that round, without playing the rounds before it."""
    parts = ("map", map_name, "fight", fight_id)
    return world.stream(*parts) if round_number is None else world.stream(*parts, "round", round_number)


class Log:
    """The events of a round so far. Adding one also lets the rules react (``Rules.after_event``)."""

    def __init__(self, fight: Fight, rules: Rules):
        self.fight, self.rules, self.events = fight, rules, []

    def add(self, kind: EventType, *data) -> None:
        self.put(event(kind, *data))

    def put(self, new: Event) -> None:
        self.events.append(new)
        for extra in self.rules.after_event(self.fight, new):
            self.put(extra)

    def extend(self, events: list[Event]) -> None:
        for each in events:
            self.put(each)


# --- the order -------------------------------------------------------------------------------

def participants(fight: Fight, rules: Rules, rng: random.Random) -> list[Address]:
    """Who acts, fastest first. A fighter appears once for each blow of a multi-strike weapon, spaced through its speed."""
    pool: dict[int, list[Address]] = {}
    for address in fight.addresses():
        fighter = fight.get(address)
        if not fighter.alive(rules) or fighter.command == Command.DEFEND:
            continue
        casting = None
        if fighter.command in (Command.SKILL, Command.SPELL) and 0 <= fighter.using < len(fighter.abilities):
            casting = fighter.abilities[fighter.using]
        speed = rules.speed(rng, fighter.get_current(rules, "Speed"), casting, fighter.get_current(rules, "Focus") if casting else 0)
        times = 1
        if fighter.command == Command.ATTACK_LEFT and fighter.equipment.get("lhand") is not None:
            times = fighter.inventory[fighter.equipment["lhand"]][0].attack_count
        elif fighter.command == Command.ATTACK_RIGHT and fighter.equipment.get("rhand") is not None:
            times = fighter.inventory[fighter.equipment["rhand"]][0].attack_count
        for count in range(1, times + 1):
            pool.setdefault(math.floor(speed * count / times), []).append(address)
    order: list[Address] = []
    for speed in sorted(pool, reverse=True):
        rung = pool[speed]
        rng.shuffle(rung)  # the same speed: any order
        order.extend(rung)
    return order


def do_combat(fight: Fight, rules: Rules, rng: random.Random) -> list[Event]:
    """Plays one round and returns its events. Stops early once only one party is left standing."""
    log = Log(fight, rules)
    queue = participants(fight, rules, rng)
    while queue and fight.live_parties(rules) > 1:
        address = queue.pop(0)
        fighter = fight.get(address)
        if not fighter.alive(rules):
            continue
        perform_action(fight, rules, rng, address, fighter, log)
    return log.events


# --- one fighter's turn ----------------------------------------------------------------------------

def valid_action(fighter: Combatant) -> bool:
    command = fighter.command
    if command == Command.ATTACK_LEFT:
        return True
    if command == Command.ATTACK_RIGHT:
        return not fighter.hands_share_weapon()
    if command == Command.ITEM:
        return 0 <= fighter.using < len(fighter.inventory)
    if command in (Command.SKILL, Command.SPELL):
        return 0 <= fighter.using < len(fighter.abilities)
    if command in (Command.EQUIP, Command.EQUIP_AMMO):
        # As DragonStar checks it: loading ammunition needs target[0] to be a real stack too.
        if command == Command.EQUIP_AMMO and not 0 <= fighter.target[0] < len(fighter.inventory):
            return False
        index = fighter.target[0] if fighter.using == -1 else fighter.using
        return 0 <= index < len(fighter.inventory)
    return command in (Command.DEFEND, Command.RUN)


def perform_action(fight: Fight, rules: Rules, rng: random.Random, address: Address, fighter: Combatant, log: Log) -> None:
    log.add(EventType.TURN, *address)
    if not valid_action(fighter):
        return
    command = fighter.command
    may_affect = True
    ammo_used: bool | int = True
    effect: EffectSpec | None = None
    ability = item = None

    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        side_word = "left" if command == Command.ATTACK_LEFT else "right"
        effect = fighter.weapon_effect(rules, command)
        if lost_target(fight, rules, rng, fighter, effect):
            return
        held = fighter.equipment.get(side_word[0] + "hand")
        item_key = fighter.inventory[held][0].key if held is not None else None
        ammo_used = fighter.expend_ammo(command)
        if ammo_used is not False:
            log.add(EventType.ATTACK, item_key, side_word, effect.targets)
        else:
            may_affect = False
            log.add(EventType.NO_AMMO, *address)
    elif command == Command.ITEM:
        item = fighter.inventory[fighter.using][0]
        effect = item.use_effect
        if lost_target(fight, rules, rng, fighter, effect):
            return
        log.add(EventType.ITEM, item.key)
    elif command in (Command.EQUIP, Command.EQUIP_AMMO):
        equip_in_fight(fighter, log)
        return
    elif command in (Command.SKILL, Command.SPELL):
        ability = fighter.abilities[fighter.using]
        effect = ability.effect
        if lost_target(fight, rules, rng, fighter, effect):
            return
        log.add(EventType.SKILL if command == Command.SKILL else EventType.SPELL, ability.key)
    elif command == Command.DEFEND:
        log.add(EventType.DEFEND)
        return
    elif command == Command.RUN:
        log.add(EventType.RUN)
        return

    if not may_affect:
        return
    log.add(EventType.TARGET, *fighter.target, effect.targets)

    if ability is not None:
        resource, amount = rules.ability_cost(ability)
        if fighter.get_current(rules, resource) < amount:
            may_affect = False
            log.add(EventType.NO_MP, *address)
        else:
            log.add(EventType.ALTER_STAT, *address, resource, -amount)
            fighter.current[resource] -= amount
    if not may_affect:
        return

    for target_address, target, intensity, divisor in list(expand(fight, rules, address, fighter.target, effect)):
        affected_by(fight, rules, rng, fighter, address, effect, target, target_address, intensity, divisor, log)
    if command == Command.ITEM and item.one_use:
        fighter.remove_item(fighter.using, 1)
        log.add(EventType.USE_ITEM, fighter.using)
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT) and not isinstance(ammo_used, bool):
        fighter.remove_item(ammo_used, 1)
        log.add(EventType.EXPEND_AMMO, ammo_used)


def equip_in_fight(fighter: Combatant, log: Log) -> None:
    """Changing gear mid-fight: wield (and, for EQUIP_AMMO, load) the stack at ``using`` on the side ``target[0]``;
    or, with ``using`` of -1, put away the stack at ``target[0]``. Whatever is in the way is taken off first."""
    location, ammo, _unused = fighter.target
    if fighter.using != -1:
        while True:
            result = fighter.equip(fighter.using, location)
            if result.outcome is EquipOutcome.SUCCESS:
                break
            if result.outcome is EquipOutcome.NEEDS_UNEQUIPPING:
                freed = fighter.unequip(result.occupying_position)
                if freed.outcome is EquipOutcome.NOT_EQUIPABLE:
                    return
                for slot in freed.slots:
                    log.add(EventType.UNEQUIP_SLOT, slot)
                continue
            return  # nothing else is expected when equipping a weapon
        for slot in result.slots:
            log.add(EventType.EQUIP_SLOT, fighter.using, slot)
        item = fighter.inventory[fighter.using][0]
        if fighter.command == Command.EQUIP_AMMO:
            if "hand" not in item.equip_slots:
                location = 0
            result = fighter.equip(ammo, location)
            if result.outcome is not EquipOutcome.SUCCESS:
                return
            for slot in result.slots:
                log.add(EventType.EQUIP_SLOT, ammo, slot)
            log.add(EventType.EQUIP, item.key, fighter.inventory[ammo][0].key)
        else:
            log.add(EventType.EQUIP, item.key)
        return
    item = fighter.inventory[location][0]
    freed = fighter.unequip(location)
    if freed.outcome in (EquipOutcome.NOT_FOUND, EquipOutcome.NOT_EQUIPABLE):
        return
    log.add(EventType.UNEQUIP, item.name)
    for slot in freed.slots:
        log.add(EventType.UNEQUIP_SLOT, slot)


# --- what an action does to one target ---------------------------------------------------------------------

def affected_by(fight, rules, rng, fighter, fighter_address, effect, target, target_address, intensity, divisor, log) -> None:
    impact = (divisor + 1 - intensity) / (divisor + 1.0)
    command = fighter.command
    vital = rules.vital
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        if target.get_current(rules, vital) == 0:
            return
        accuracy, dodge = fighter.get_current(rules, "Accuracy", command), target.get_current(rules, "Dodge")
        roll = rules.chance_to_hit(rng, accuracy, dodge)
        if roll is None:
            log.add(EventType.MISS, *target_address)
            return
        critical = rules.is_critical(roll)
        damage = rules.hit_damage(
            fighter.get_current(rules, "Strength", command), target.get_current(rules, "Block"),
            rules.hit_chance(accuracy, dodge), roll, target.command == Command.DEFEND, impact, critical,
        )
        inflict_damage(fight, rules, fighter_address, target, target_address, damage, critical, log)
    elif command == Command.ITEM:
        do_effect(fight, rules, rng, effect, fighter_address, target, target_address, impact, 1, log)
    elif command == Command.SKILL:
        if effect.detrimental:  # skills can be dodged
            if target.get_current(rules, vital) == 0:
                return
            roll = rules.chance_to_hit(rng, fighter.get_current(rules, "Accuracy"), target.get_current(rules, "Dodge"))
            if roll is None:
                log.add(EventType.MISS, *target_address)
                return
        do_effect(fight, rules, rng, effect, fighter_address, target, target_address, impact, 1, log)
    elif command == Command.SPELL:
        immunity = 1
        if effect.detrimental:  # spells can be resisted
            if target.get_current(rules, vital) == 0:
                return
            immunity = rules.saving_throw(rng, fighter.get_current(rules, "Power"), target.get_current(rules, "Resistance"))
            if immunity == 0:
                log.add(EventType.NO_EFFECT, *target_address)
                return
        do_effect(fight, rules, rng, effect, fighter_address, target, target_address, impact, immunity, log)


def do_effect(fight, rules, rng, effect, actor, target, target_address, impact, immunity, log) -> bool:
    vital = rules.vital
    if effect.effect == specs.NONE:
        return True
    if effect.effect == specs.HEAL:
        if target.get_current(rules, vital) == 0:
            return False
        restore_vital(fight, rules, actor, target, target_address, rules.roll_amount(rng, effect), log)
    elif effect.effect == specs.HURT:
        if target.get_current(rules, vital) == 0:
            return False
        damage = math.floor(rules.roll_amount(rng, effect) * immunity * impact)
        inflict_damage(fight, rules, actor, target, target_address, damage, False, log)
    elif effect.effect == specs.REVIVE:
        maximum = target.get_base(rules, vital)
        if target.get_current(rules, vital) == 0:
            restore_vital(fight, rules, actor, target, target_address, maximum, log)
        elif rules.revive_chance(rng, effect):
            restore_vital(fight, rules, actor, target, target_address, rules.revive_amount(maximum, effect), log)
    elif effect.effect == specs.RESTORE_MP:
        if target.get_current(rules, vital) == 0:
            return False
        restore_pool(fight, rules, actor, target, target_address, rules.mana, rules.roll_amount(rng, effect), log)
    # The rest (slay, stat changes, statuses) arrive with the statuses piece.
    return True


def inflict_damage(fight, rules, actor, target, target_address, damage, critical, log) -> None:
    vital = rules.vital
    before = target.current[vital]
    maximum = target.get_base(rules, vital)
    # Healing by negative damage can't take it past the maximum.
    if target.get_current(rules, vital) - damage > maximum:
        damage = target.get_current(rules, vital) - maximum
    if damage >= 0:
        log.add(EventType.DAMAGE, *target_address, damage, critical)
    else:
        log.add(EventType.RESTORE, *target_address, vital, -damage)
    target.current[vital] -= damage
    if target.current[vital] <= 0:
        log.add(EventType.DIED, *target_address, damage, -target.current[vital])
        target.current[vital] = 0
    log.extend(rules.gauge_moved(fight, actor, target_address, vital, before, target.current[vital], maximum))


def restore_vital(fight, rules, actor, target, target_address, amount, log) -> None:
    vital = rules.vital
    before = target.current[vital]
    maximum = target.get_base(rules, vital)
    if target.get_current(rules, vital) + amount > maximum:
        amount = maximum - target.get_current(rules, vital)
    target.current[vital] += amount
    if amount < 0:
        log.add(EventType.DAMAGE, *target_address, -amount, False)
        if target.current[vital] <= 0:
            overkill = -target.current[vital]
            target.current[vital] = 0
            log.add(EventType.DIED, *target_address, -amount, overkill)
    else:
        if target.current[vital] > 0 and before == 0:
            log.add(EventType.REVIVED, *target_address)
        log.add(EventType.RESTORE, *target_address, vital, amount)
    log.extend(rules.gauge_moved(fight, actor, target_address, vital, before, target.current[vital], maximum))


def restore_pool(fight, rules, actor, target, target_address, resource, amount, log) -> None:
    before = target.current[resource]
    maximum = target.get_base(rules, resource)
    if target.get_current(rules, resource) + amount > maximum:
        amount = maximum - target.get_current(rules, resource)
    if amount < 0:
        raise ValueError(f"restoring {resource} came to {amount} for {target.name}: a restore never takes away")
    log.add(EventType.RESTORE, *target_address, resource, amount)
    target.current[resource] += amount
    log.extend(rules.gauge_moved(fight, actor, target_address, resource, before, target.current[resource], maximum))
