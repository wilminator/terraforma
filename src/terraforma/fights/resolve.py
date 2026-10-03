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
from . import specs, status
from .combatant import Address, Combatant, Command
from .events import Event, EventType, event
from .fight import Fight
from .gear import EquipOutcome
from .rules import AskPlayer, Rules
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
        #: Above 0 while a status tick is playing: what it does doesn't set off other ticks (harmed, helped).
        self.ticking = 0

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
    """Plays one round and returns its events. Stops early once only one party is left standing, and then pays the
    fight out (once: a finished fight plays no more rounds)."""
    if fight.over:
        return []
    log = Log(fight, rules)
    for address in fight.addresses():
        fire(fight, rules, rng, address, status.ROUND_START, log)
    queue = participants(fight, rules, rng)
    while queue and fight.live_parties(rules) > 1:
        address = queue.pop(0)
        fighter = fight.get(address)
        if not fighter.alive(rules):
            continue
        perform_action(fight, rules, rng, address, fighter, log)
    for address in fight.addresses():
        fire(fight, rules, rng, address, status.ROUND_END, log)
    end_round(fight, rules, log)
    if rules.fight_is_over(fight):
        fight.over = True
        log.add(EventType.FIGHT_OVER)
        log.extend(rules.on_fight_end(fight, rng))
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
    """One fighter's turn: it starts (and may be lost to a status), the fighter acts, and it ends."""
    log.add(EventType.TURN, *address)
    status.count_turn(fighter)
    if fire(fight, rules, rng, address, status.TURN_START, log) or not fighter.alive(rules):
        return
    act(fight, rules, rng, address, fighter, log)
    if fighter.alive(rules):
        fire(fight, rules, rng, address, status.TURN_END, log)


def act(fight: Fight, rules: Rules, rng: random.Random, address: Address, fighter: Combatant, log: Log) -> None:
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

    for target_address, target, intensity, divisor in list(expand(fight, rules, address, fighter.target, effect, rng)):
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
        damage = status.adjusted_damage(fighter, target, damage, effect.attribute)
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
            fire(fight, rules, rng, target_address, status.SAVING_THROW, log)
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
        damage = status.adjusted_damage(fight.get(actor), target, damage, effect.attribute)
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
    elif effect.effect == specs.SLAY:
        if target.get_current(rules, vital) == 0:
            return False
        if rules.slay_chance(rng, effect):
            inflict_damage(fight, rules, actor, target, target_address, target.current[vital], False, log)
        else:
            log.add(EventType.NO_EFFECT, *target_address)
    elif effect.effect in (specs.CAUSE_GOOD_STATUS, specs.CAUSE_BAD_STATUS):
        if target.get_current(rules, vital) == 0:
            return False
        place_status(fight, effect, actor, target, target_address, log)
    elif effect.effect in (specs.REMOVE_GOOD_STATUS, specs.REMOVE_BAD_STATUS):
        if target.get_current(rules, vital) == 0:
            return False
        kind = status.GOOD if effect.effect == specs.REMOVE_GOOD_STATUS else status.BAD
        for token in list(target.tokens):
            if token.spec.kind == kind and effect.status in ("", token.spec.key) and not token.unremovable:
                remove_token(target, target_address, token, status.REMOVED, log)
    return True


def gauge_changed(fight, rules, actor, target_address, resource, before, after, maximum, log) -> None:
    """One fighter moved another's gauge: the rules react (``Rules.gauge_moved``: experience), and, if the target is on
    a party that is not its enemy (an ally, a neutral, or its own party on another team), may move the teams' relationship (``Rules.relation_moved``)."""
    log.extend(rules.gauge_moved(fight, actor, target_address, resource, before, after, maximum))
    if before == after:
        return
    allies, enemies = rules.alignment(fight, actor[0])
    if target_address[0] in enemies and target_address[0] not in allies:
        return
    answer = rules.relation_moved(fight, actor, target_address, resource, before, after, maximum)
    team, other = fight.team_of(actor), fight.team_of(target_address)
    if not answer or team is None or other is None or team == other:
        return
    if isinstance(answer, AskPlayer):  # the owner of the actor's team is asked, after the fight
        log.add(EventType.RELATION_PROMPT, team, other, answer.delta, answer.reason)
    else:
        log.add(EventType.RELATION_CHANGE, team, other, answer)


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
    gauge_changed(fight, rules, actor, target_address, vital, before, target.current[vital], maximum, log)
    if damage != 0 and not log.ticking:
        fire(fight, rules, None, target_address, status.HARMED if damage > 0 else status.HELPED, log)


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
    gauge_changed(fight, rules, actor, target_address, vital, before, target.current[vital], maximum, log)
    if amount > 0 and not log.ticking:
        fire(fight, rules, None, target_address, status.HELPED, log)


def restore_pool(fight, rules, actor, target, target_address, resource, amount, log) -> None:
    before = target.current[resource]
    maximum = target.get_base(rules, resource)
    if target.get_current(rules, resource) + amount > maximum:
        amount = maximum - target.get_current(rules, resource)
    if amount < 0:
        raise ValueError(f"restoring {resource} came to {amount} for {target.name}: a restore never takes away")
    log.add(EventType.RESTORE, *target_address, resource, amount)
    target.current[resource] += amount
    gauge_changed(fight, rules, actor, target_address, resource, before, target.current[resource], maximum, log)
    if amount > 0 and not log.ticking:
        fire(fight, rules, None, target_address, status.HELPED, log)


# --- statuses ------------------------------------------------------------------------------------------------------

def place_status(fight: Fight, effect: EffectSpec, source: Address, target: Combatant, target_address: Address, log: Log) -> None:
    spec = fight.statuses.get(effect.status)
    if spec is None:
        raise ValueError(f"the fight does not know the status {effect.status!r}")
    duration = effect.duration if effect.duration is not None else spec.duration
    log.add(EventType.STATUS_APPLIED, *target_address, spec.key, *source, duration)
    status.place(target, spec, source, duration)


def remove_token(fighter: Combatant, address: Address, token: status.StatusToken, reason: str, log: Log) -> None:
    log.add(EventType.STATUS_REMOVED, *address, token.spec.key, *token.source, reason)
    status.take_off(fighter, token.spec.key, token.source)


def fire(fight: Fight, rules: Rules, rng: random.Random | None, address: Address, when: str, log: Log) -> bool:
    """Plays the ticks of every token on the fighter that are due at $when. True if one of them takes the turn."""
    fighter = fight.get(address)
    if not fighter.alive(rules):
        return False
    skipped = False
    for token in list(fighter.tokens):
        for tick in token.spec.ticks:
            if token not in fighter.tokens or not fighter.alive(rules) or not status.due(token, tick, when):
                continue
            skipped |= tick_token(fight, rules, rng, address, fighter, token, tick, when, log)
    return skipped


def tick_token(fight, rules, rng, address, fighter, token, tick, when, log) -> bool:
    """One tick of one token. The source is the actor behind whatever it does, so the experience rules credit it."""
    intensity = token.intensity
    log.add(EventType.STATUS_TICK, *address, token.spec.key, *token.source, round(intensity, 4), when)
    log.ticking += 1
    try:
        moved = False
        if tick.action == status.SKIP_TURN:
            if when == status.TURN_START and rng is not None and status.skips_turn(token, tick, rng):
                log.add(EventType.TURN_SKIPPED, *address, token.spec.key)
                skipped = True
            else:
                skipped = False
            if skipped:
                log.extend(rules.status_acted(fight, token.source, address, token.spec, intensity, token.spec.xp_share * intensity))
            return skipped
        if tick.action == status.END:
            remove_token(fighter, address, token, status.ENDED, log)
        elif tick.action in (status.DAMAGE, status.HEAL):
            resource = tick.resource or rules.vital
            amount = status.tick_amount(tick, fighter.get_base(rules, resource), intensity)
            source = fight.get(token.source) if token.source in fight.addresses() else None
            if tick.action == status.DAMAGE:
                amount = status.adjusted_damage(source, fighter, amount, tick.attribute)
            if amount > 0:
                moved = True
                if resource == rules.vital:
                    if tick.action == status.DAMAGE:
                        inflict_damage(fight, rules, token.source, fighter, address, amount, False, log)
                    else:
                        restore_vital(fight, rules, token.source, fighter, address, amount, log)
                else:
                    before, maximum = fighter.current[resource], fighter.get_base(rules, resource)
                    now = min(max(before + (-amount if tick.action == status.DAMAGE else amount), 0), maximum)
                    if now != before:
                        log.add(EventType.ALTER_STAT, *address, resource, now - before)
                        fighter.current[resource] = now
                        gauge_changed(fight, rules, token.source, address, resource, before, now, maximum, log)
        if not moved and token.spec.xp_share > 0:
            log.extend(rules.status_acted(fight, token.source, address, token.spec, intensity, token.spec.xp_share * intensity))
        return False
    finally:
        log.ticking -= 1


def end_round(fight: Fight, rules: Rules, log: Log) -> None:
    """Closes a round for the statuses: the dead lose theirs, the rest age and the ones that have run their course
    end."""
    for address in fight.addresses():
        fighter = fight.get(address)
        if not fighter.alive(rules):
            for token in list(fighter.tokens):
                remove_token(fighter, address, token, status.DIED, log)
    if any(fight.get(address).tokens for address in fight.addresses()):
        log.add(EventType.ROUND_END)
        for address in fight.addresses():
            status.age(fight.get(address))
        for address in fight.addresses():
            fighter = fight.get(address)
            for token in status.expired(fighter):
                remove_token(fighter, address, token, status.EXPIRED, log)
