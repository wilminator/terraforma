"""A fighter's potential experience (PXP): one number for how strong it is.

DragonStar's formula, kept as it is. It rates a fighter on four parts and adds
them: physical defence (Dodge, Block and HP), physical offence (what each hand
swings, with the ammunition it has), magical defence (Resistance and HP) and
magical offence (the spells and skills it can pay for, with Speed, Focus and
Power). Gear counts; statuses do not. The experience tree weighs each blow by
it, and the AI's protector goal uses it.

``Rules.pxp`` calls ``potential``; a game with other stats overrides that.
"""

import math

from .combatant import Combatant
from .gear import find_slot, round_half_up
from .rules import Rules
from .specs import (
    ALL_ALLIES,
    ALL_ENEMIES,
    ALL_NOT_ALLIES,
    ALL_NOT_ENEMIES,
    ALL_PARTIES,
    CAUSE_BAD_STATUS,
    CAUSE_GOOD_STATUS,
    GROUP,
    HEAL,
    HURT,
    PARTY,
    RANDOM_PARTY,
    REMOVE_BAD_STATUS,
    REMOVE_GOOD_STATUS,
    REVIVE,
    SLAY,
    EffectSpec,
)


def target_rating(targets: int) -> float:
    """How much a reach is worth: one target 1, a group 3, a party 9, everyone 27, and a little for neighbours."""
    if targets == GROUP:
        return 3
    if targets in (PARTY, RANDOM_PARTY):
        return 9
    if targets in (ALL_PARTIES, ALL_ENEMIES, ALL_ALLIES, ALL_NOT_ENEMIES, ALL_NOT_ALLIES):
        return 27
    return targets * 0.5 + 1 if targets < 3 else 2.5


def effect_rating(effect: EffectSpec) -> float:
    """How much an effect is worth."""
    kind, base, added = effect.effect, effect.base, effect.added
    if kind in (HEAL, HURT):
        return base + added / 2
    if kind in (REVIVE, SLAY):
        return base * 2.5 + added * 2.5
    if kind in (CAUSE_GOOD_STATUS, REMOVE_GOOD_STATUS, CAUSE_BAD_STATUS, REMOVE_BAD_STATUS):
        return base * added
    return 0


def half_life(start: int, end: int | None) -> float:
    """How many turns' worth of use a stock of ammunition gives between ``start`` and ``end`` (None: no end)."""
    final = 2 if end is None else 2 - 0.5 ** (end - 1)
    if start < 1:
        return final
    return final - (2 - 0.5 ** (start - 1))


def _speed_multiplier(times: int) -> float:
    return (times + 1) / 2.0


def _cbrt(value: float) -> float:
    return value ** (1.0 / 3)


def _hand(rules: Rules, fighter: Combatant, index: int):
    """What the hand (0 left, 1 right, 2 bare) swings: (ammo count, reach rating, speed, accuracy, strength) or None."""
    if index < 2:
        weapon = fighter.weapon(rules, index)
        if weapon is None:
            return None
        ammo_at = fighter.equipment.get(find_slot("ammo", index))
        ammo_count = None if ammo_at is None else fighter.inventory[ammo_at][1]
        effect = fighter.weapon_effect(rules, index)
    else:
        weapon = rules.unarmed
        ammo_count = None
        effect = weapon.weapon_effect(fighter.get_current(rules, "Strength", index))
    times = weapon.attack_count
    if ammo_count is not None:
        ammo_count = math.ceil(ammo_count / times)
    strength = max(1, fighter.get_base(rules, "Strength", index))
    speed = max(1, fighter.get_base(rules, "Speed", index)) * _speed_multiplier(times)
    accuracy = max(1, fighter.get_base(rules, "Accuracy", index))
    return ammo_count, target_rating(effect.targets), speed, accuracy, strength


def _offense(hands: dict) -> int:
    scores = []
    for index in (0, 1):
        if index not in hands:
            continue
        ammo, reach, speed, accuracy, strength = hands[index]
        turn = ammo
        score = _cbrt(speed) * _cbrt(accuracy) * _cbrt(reach * strength) * half_life(0, turn)
        if turn is None:
            scores.append(score)
            continue
        if 1 - index not in hands:  # no other weapon to fall back on: this hand scores nothing
            continue
        ammo, reach, speed, accuracy, strength = hands[1 - index]
        if ammo is not None:
            ammo += turn
        multiplier = half_life(turn, ammo)
        turn = ammo
        score += _cbrt(speed) * _cbrt(accuracy) * _cbrt(reach * strength) * multiplier
        if turn is None:
            scores.append(score)
            continue
        _, reach, speed, accuracy, strength = hands[2]  # then bare hands
        score += _cbrt(speed) * _cbrt(accuracy) * _cbrt(reach * strength) * half_life(turn, None)
        scores.append(score)
    return round_half_up(sum(scores) / max(1, len(scores)))


def potential(rules: Rules, fighter: Combatant) -> int:
    """The fighter's PXP."""

    def base(stat: str) -> int:
        return max(1, fighter.get_base(rules, stat, True))

    hp = fighter.get_base(rules, "HP", True)
    mp = fighter.get_base(rules, "MP", True)
    physical_defense = round_half_up(_cbrt(base("Dodge")) * _cbrt(base("Block")) * _cbrt(hp))
    hands = {index: hand for index in (0, 1, 2) if (hand := _hand(rules, fighter, index)) is not None}
    physical_offense = _offense(hands)
    magical_defense = round_half_up(math.sqrt(base("Resistance")) * math.sqrt(hp))
    spells = skills = 0.0
    for ability in fighter.abilities:
        usable = 1 if mp >= ability.mp_cost else 0
        use = usable * math.sqrt(target_rating(ability.effect.targets) * effect_rating(ability.effect))
        if ability.kind == "spell":
            spells += use
        else:
            skills += use
    speed, focus, power, accuracy = base("Speed"), base("Focus"), base("Power"), base("Accuracy")
    spells = round_half_up(spells**0.25 * speed**0.25 * focus**0.25 * power**0.25)
    skills = round_half_up(_cbrt(skills) * _cbrt(speed) * _cbrt(accuracy))
    return physical_defense + physical_offense + magical_defense + spells + skills
