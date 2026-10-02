"""Who an action reaches, and what happens when its target has gone."""

import random
from collections.abc import Iterator

from . import specs
from .combatant import Address, Combatant, Command
from .fight import Fight, Group
from .rules import Rules
from .specs import EffectSpec


def _skip(rules: Rules, effect: EffectSpec, character: Combatant) -> bool:
    return effect.only_living and not character.alive(rules)


def _whole_group(party: int, group_index: int, group: Group, rules: Rules, effect: EffectSpec) -> Iterator[tuple]:
    for character_index, character in group.characters.items():
        if not _skip(rules, effect, character):
            yield (party, group_index, character_index), character, 0, 0


def _whole_party(fight: Fight, party: int, rules: Rules, effect: EffectSpec) -> Iterator[tuple]:
    for group_index, group in fight.parties[party].groups.items():
        yield from _whole_group(party, group_index, group, rules, effect)


def expand(fight: Fight, rules: Rules, actor: Address, target: Address, effect: EffectSpec) -> Iterator[tuple]:
    """Every fighter the effect reaches, as (address, fighter, intensity, divisor): (0, 0) for an unscaled hit, or
    (distance, range) for one that falls off with distance from the centre of a ranged attack."""
    scope = effect.targets
    party, group_index, character_index = target
    if scope == specs.ALL_PARTIES:
        for each in fight.parties:
            yield from _whole_party(fight, each, rules, effect)
        return
    if scope == specs.ALL_ENEMIES:
        for each in fight.parties:
            if not rules.is_ally(actor[0], each):
                yield from _whole_party(fight, each, rules, effect)
        return
    if scope == specs.ALL_ALLIES:
        for each in fight.parties:
            if rules.is_ally(actor[0], each):
                yield from _whole_party(fight, each, rules, effect)
        return
    if party not in fight.parties:
        return
    if scope == specs.PARTY:
        yield from _whole_party(fight, party, rules, effect)
        return
    group = fight.parties[party].groups.get(group_index)
    if group is None:
        return
    if scope == specs.GROUP:
        yield from _whole_group(party, group_index, group, rules, effect)
        return
    # One target, or a range of neighbours along the group either side of it.
    if character_index not in group.characters:
        return
    reach = max(0, scope)
    for offset in range(reach + 1):
        if offset > 0 and character_index - offset >= 0:
            other = group.characters.get(character_index - offset)
            if other is not None and not _skip(rules, effect, other):
                yield (party, group_index, character_index - offset), other, offset, reach
        if character_index + offset < len(group.characters):
            other = group.characters.get(character_index + offset)
            if other is not None and not _skip(rules, effect, other):
                yield (party, group_index, character_index + offset), other, offset, reach


def lost_target(fight: Fight, rules: Rules, rng: random.Random, fighter: Combatant, effect: EffectSpec) -> bool:
    """True if the action can't be done because its target is gone. If the target is dead but others
    nearby live, the fighter picks one of them at random instead and the action goes ahead (False)."""
    if fighter.command in (Command.EQUIP, Command.EQUIP_AMMO, Command.RUN, Command.DEFEND):
        return False
    if not effect.only_living:
        return False
    scope = effect.targets
    party, group_index, character_index = fighter.target
    if scope in (specs.ALL_PARTIES, specs.ALL_ENEMIES, specs.ALL_ALLIES):
        return False
    if scope == specs.PARTY:
        return party not in fight.parties or fight.parties[party].dead(rules)
    if party not in fight.parties:
        return True
    groups = fight.parties[party].groups
    if scope == specs.GROUP:
        if group_index in groups and not groups[group_index].dead(rules):
            return False
        if fight.parties[party].dead(rules):
            return True
        living = [index for index, group in groups.items() if not group.dead(rules)]
        fighter.target = (party, rng.choice(living), character_index)
        return False
    if group_index not in groups:
        return True
    group = groups[group_index]
    reach = max(0, scope)
    near = [group.characters.get(index + character_index) for index in range(-reach, reach + 1)]
    if any(other is not None and other.alive(rules) for other in near):
        return False
    if group.dead(rules):
        return True
    living = [index for index, other in group.characters.items() if other.alive(rules)]
    fighter.target = (party, group_index, rng.choice(living))
    return False
