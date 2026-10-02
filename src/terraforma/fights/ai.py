"""How the computer plays a fighter: monsters, NPCs and heroes on autopilot.

Pure, no database or web code. This is DragonStar's AI, carried over as the
engine's default ("the process mirrored, functionally equivalent"). A fighter's
numbers are its ``ai_action``, ``ai_goal``, ``ai_target`` and ``ai_experience``
(a monster's ``ai`` block in the seed); what they mean is defined here.

Every random choice draws from the ``random.Random`` it is handed, which is the
fight's own stream, so a fight replays exactly.

A command is chosen in four steps:

1. the *action* (what kind of player it is) picks a command to try: an attack
   with a hand, an item, a skill or a spell;
2. the *goal* (what it wants) values that command on every fighter: positive
   for the ones it wants to affect, negative for the rest;
3. those values are added up by how far the command reaches (one target, a
   group, a party, everyone);
4. the *target* level (how well it judges) keeps only the best share of the
   list, and one of the best is picked at random.

Where this departs from DragonStar's code, on purpose, to do what DragonStar
says it means:

* ``perceived``: DragonStar divides by the random factor, so a fighter reads
  every stat as a hundredth of its value. Its notes say the stat is skewed by up
  to ``ai_experience`` percent; that is what is done here.
* A command that reaches ``n`` neighbours adds up the neighbours' values;
  DragonStar adds the target's own value once for each neighbour.

Kept as DragonStar has them (odd, but the numbers are the game's): ``revive`` is
worth a negative number against a dead ally, and ``restore_mp`` is worth the
square of what it restores.
"""

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from enum import IntEnum

from .combatant import Address, Combatant, Command
from .fight import Fight
from .gear import round_half_up
from .rules import Rules
from .specs import (
    ALL_ALLIES,
    ALL_ENEMIES,
    ALL_NOT_ALLIES,
    ALL_NOT_ENEMIES,
    ALL_PARTIES,
    DETRIMENTAL,
    GROUP,
    HEAL,
    HURT,
    NONE,
    ONLY_LIVING,
    PARTY,
    RANDOM_PARTY,
    RESTORE_MP,
    REVIVE,
    SLAY,
    AbilitySpec,
    EffectSpec,
)

NO_TARGET_COMMANDS = (Command.EQUIP, Command.DEFEND, Command.RUN, Command.EQUIP_AMMO)


class Action(IntEnum):
    """How a fighter plays: ``ai_action``."""

    STUPID = 0
    NORMAL = 1
    HEALER = 2
    PROTECTOR = 3  # plays as a healer until statuses exist
    PUMMELER = 4
    FIGHTER = 5
    HINDERER = 6  # plays as a fighter until statuses exist
    CASTER = 7
    MAGE = 8
    SHARP = 9
    SMART = 10  # plays as sharp until statuses exist
    OMNIPOTENT = 11  # plays as sharp until statuses exist


class Goal(IntEnum):
    """What a fighter wants: ``ai_goal``."""

    RANDOM = 0
    DESTRUCTOR = 1
    SCHEMER = 2
    PREVENTOR = 3
    PROTECTOR = 4


class Aim(IntEnum):
    """How well a fighter judges its target: ``ai_target``."""

    STUPID = 0
    VULTURE = 1
    NORMAL = 2
    GROUP = 3
    SMART = 4
    TEAM = 5
    WISE = 6
    OMNIPOTENT = 7


@dataclass(frozen=True)
class Choice:
    command: int
    using: int
    #: Who or what it is aimed at; zeros for what has no single target.
    target: Address


Key = tuple[int, int, int, int, int]  # command, using, target party, group, character
Values = dict[int, dict[int, dict[int, float]]]
Option = tuple[int, int]  # command, using


# --- Profiles: the numbers a fighter's AI is set to -----------------------------------------

LEVEL_JITTER = 3
EXP_RECRUIT, EXP_PRIVATE, EXP_SERGEANT, EXP_LIEUTENANT, EXP_COLONEL, EXP_GENERAL, EXP_OMNIPOTENT = 50, 40, 30, 20, 10, 5, 0


@dataclass(frozen=True)
class Profile:
    action: int
    goal: int
    target: int
    experience: int

    def apply(self, fighter: Combatant) -> None:
        fighter.ai_action, fighter.ai_goal, fighter.ai_target, fighter.ai_experience = (
            int(self.action), int(self.goal), int(self.target), self.experience,
        )  # fmt: skip


def goal_for(action: int) -> int:
    if action == Action.HEALER:
        return Goal.PROTECTOR
    if action == Action.CASTER:
        return Goal.SCHEMER
    return Goal.DESTRUCTOR


def specialty(abilities: Sequence[AbilitySpec]) -> int:
    """What a fighter's combat abilities make it best at.

    Healer when 40% or more heal or revive; Mage or Caster when mostly spells
    (Mage if they mostly hurt); Pummeler when mostly hurting skills; else Fighter.
    """
    heal = hurt_spell = other_spell = hurt_skill = other_skill = 0
    for ability in abilities:
        effect = ability.effect.effect
        if effect == NONE:
            continue
        spell = ability.kind == "spell"
        if effect in (HEAL, REVIVE):
            heal += 1
        elif effect in (HURT, SLAY):
            hurt_spell, hurt_skill = hurt_spell + spell, hurt_skill + (not spell)
        else:
            other_spell, other_skill = other_spell + spell, other_skill + (not spell)
    total = heal + hurt_spell + other_spell + hurt_skill + other_skill
    if total == 0:
        return Action.FIGHTER
    if heal * 5 >= total * 2:
        return Action.HEALER
    if hurt_spell + other_spell > hurt_skill + other_skill:
        return Action.MAGE if hurt_spell >= other_spell else Action.CASTER
    return Action.PUMMELER if hurt_skill > 0 else Action.FIGHTER


def profile_for_level(
    level: int, abilities: Sequence[AbilitySpec], *, player: bool, rng: random.Random, jitter: int | None = None
) -> Profile:
    """The AI numbers for a fighter of ``level``, give or take up to LEVEL_JITTER levels.

    Under 5 Stupid; 5-9 Normal; 10-19 its specialty; 20-39 Sharp with sharper
    targeting and experience; 40-49 Smart; 50 and up Omnipotent. A player's
    heroes stop at Sharp, Wise and General experience. ``jitter`` 0 gives the
    plain recommendation (for an editor).
    """
    if jitter is None:
        jitter = rng.randint(-LEVEL_JITTER, LEVEL_JITTER)
    level = max(1, level + jitter)
    best = specialty(abilities)
    goal = goal_for(best)
    if level < 5:
        profile = Profile(Action.STUPID, Goal.RANDOM, Aim.STUPID, EXP_RECRUIT)
    elif level < 10:
        profile = Profile(Action.NORMAL, Goal.RANDOM, Aim.NORMAL, EXP_PRIVATE)
    elif level < 20:
        profile = Profile(best, goal, Aim.SMART, EXP_SERGEANT)
    elif level < 30:
        profile = Profile(Action.SHARP, goal, Aim.WISE, EXP_LIEUTENANT)
    elif level < 40:
        profile = Profile(Action.SHARP, goal, Aim.WISE, EXP_COLONEL)
    elif level < 50:
        profile = Profile(Action.SMART, goal, Aim.WISE, EXP_GENERAL)
    else:
        profile = Profile(Action.OMNIPOTENT, goal, Aim.OMNIPOTENT, EXP_OMNIPOTENT)
    if player:
        profile = Profile(
            min(profile.action, Action.SHARP), profile.goal, min(profile.target, Aim.WISE), max(profile.experience, EXP_GENERAL)
        )
    return profile


# --- Valuing a command on a fighter -------------------------------------------------------


def _usable(fighter: Combatant, command: int, using: int) -> EffectSpec | None:
    """What the command does when it is an item, skill or spell the fighter really has."""
    if command == Command.ITEM and 0 <= using < len(fighter.inventory):
        return fighter.inventory[using][0].use_effect
    if command in (Command.SKILL, Command.SPELL) and 0 <= using < len(fighter.abilities):
        return fighter.abilities[using].effect
    return None


def command_effect(rules: Rules, fighter: Combatant, command: int, using: int) -> EffectSpec | None:
    """What a command does; None when it names something the fighter does not have."""
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return fighter.weapon_effect(rules, command)
    if command in NO_TARGET_COMMANDS:
        return EffectSpec()
    return _usable(fighter, command, using)


def perceived(fighter: Combatant, target: Combatant, rules: Rules, stat: str, rng: random.Random) -> float:
    """A stat of the target as the fighter reads it: skewed by up to its ``ai_experience`` percent (see above)."""
    spread = fighter.ai_experience * 100
    return target.get_current(rules, stat) * rng.randint(10000 - spread, 10000 + spread) / 10000


def _attack_damage(rules: Rules, fighter: Combatant, target: Combatant, command: int, rng: random.Random) -> float:
    strength = fighter.get_current(rules, "Strength", command)
    block = perceived(fighter, target, rules, "Block", rng)
    return max(1, strength - math.floor(block / 2))


def _attack_success(rules: Rules, fighter: Combatant, target: Combatant, command: int, rng: random.Random) -> float:
    accuracy = fighter.get_current(rules, "Accuracy", command)
    dodge = perceived(fighter, target, rules, "Dodge", rng)
    return accuracy / (accuracy + dodge) if accuracy + dodge else 0.0


def _cast_success(rules: Rules, fighter: Combatant, target: Combatant, rng: random.Random) -> float:
    power = fighter.get_current(rules, "Power")
    resistance = perceived(fighter, target, rules, "Resistance", rng)
    total = power + resistance
    return power / total + min(power, resistance) / total / 2 if total else 0.0


def _effect_damage(rules: Rules, target: Combatant, effect: EffectSpec) -> float:
    vital = rules.vital
    hp, maximum = target.current[vital], target.get_base(rules, vital)
    if effect.effect in (HEAL, HURT):
        return 0 if hp == 0 else effect.added / 2 + effect.base
    if effect.effect == REVIVE:
        if hp == 0:
            return hp - maximum
        return round_half_up(maximum * 400.0 / effect.added) if effect.added else 0
    if effect.effect == RESTORE_MP:
        if hp == 0:
            return 0
        amount = effect.added / 2 + effect.base
        return amount * amount
    return 0  # nothing, or an effect the AI does not value yet


def _destructor(rules: Rules, fighter: Combatant, target: Combatant, command: int, using: int, rng: random.Random) -> float:
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return _attack_damage(rules, fighter, target, command, rng)
    effect = _usable(fighter, command, using)
    return 0 if effect is None else _effect_damage(rules, target, effect)


def _schemer(rules: Rules, fighter: Combatant, target: Combatant, command: int, using: int, rng: random.Random) -> float:
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return _attack_damage(rules, fighter, target, command, rng) * _attack_success(rules, fighter, target, command, rng)
    effect = _usable(fighter, command, using)
    if effect is None:
        return 0
    value = _effect_damage(rules, target, effect)
    if command == Command.SKILL:
        return value * _attack_success(rules, fighter, target, command, rng)
    if command == Command.SPELL:
        return value * _cast_success(rules, fighter, target, rng)
    return value


def goal_value(
    rules: Rules,
    fighter: Combatant,
    target: Combatant,
    command: int,
    using: int,
    alignment: bool | None,
    effect: EffectSpec,
    rng: random.Random,
) -> float:
    """What a command is worth against one target: positive to aim at it, negative to avoid it.

    ``alignment`` is True for an ally (or itself), False for an enemy, None for a neutral party.
    """
    if effect.effect in ONLY_LIVING and not target.alive(rules):
        return 0
    detrimental = effect.effect in DETRIMENTAL
    weight = 1 if (alignment is True and not detrimental) or (alignment is False and detrimental) else -1
    goal = fighter.ai_goal
    if goal == Goal.RANDOM:
        value = rng.randint(1, 1000)
    elif goal == Goal.DESTRUCTOR:
        value = _destructor(rules, fighter, target, command, using, rng)
    elif goal == Goal.SCHEMER:
        value = _schemer(rules, fighter, target, command, using, rng)
    elif goal == Goal.PREVENTOR:
        hp = target.current[rules.vital]
        value = 0 if hp == 0 else math.floor(_destructor(rules, fighter, target, command, using, rng) / hp * 1000)
    elif goal == Goal.PROTECTOR:
        value = rules.pxp(target) * target.current[rules.vital]
    else:
        return 0
    return value * weight


# --- The fight as the AI reads it -----------------------------------------------------------


def _alignment(rules: Rules, fight: Fight, party: int, toward: int) -> bool | None:
    allies, enemies = rules.alignment(fight, party)
    if toward in allies:
        return True
    return False if toward in enemies else None


def weak_links(rules: Rules, fight: Fight, party: int, percent: int) -> list[Address]:
    """The living fighters of ``party`` at ``percent`` of their life or less."""
    weak = []
    vital = rules.vital
    for group_index, group in fight.parties[party].groups.items():
        for char_index, fighter in group.characters.items():
            ratio = fighter.current[vital] * 100 / fighter.get_base(rules, vital)
            if 0 < ratio <= percent:
                weak.append((party, group_index, char_index))
    return weak


def command_values(
    rules: Rules,
    fight: Fight,
    fighter: Combatant,
    address: Address,
    command: int,
    using: int,
    rng: random.Random,
    only: Sequence[Address] | None = None,
) -> Values:
    """The command's worth on every fighter (0 on the ones not in ``only``, when that is given)."""
    effect = command_effect(rules, fighter, command, using)
    if effect is None:
        return {}
    values: Values = {}
    for party_index, party in fight.parties.items():
        alignment = _alignment(rules, fight, address[0], party_index)
        for group_index, group in party.groups.items():
            for char_index, target in group.characters.items():
                where = (party_index, group_index, char_index)
                if only is not None and where not in only:
                    value = 0
                else:
                    value = goal_value(rules, fighter, target, command, using, alignment, effect, rng)
                values.setdefault(party_index, {}).setdefault(group_index, {})[char_index] = value
    return values


def combine_on_reach(command: int, using: int, reach: int, values: Values) -> dict[Key, float]:
    """Add the values up by how far the command reaches: one key per thing it can be aimed at."""
    if reach in (ALL_PARTIES, ALL_ENEMIES, ALL_ALLIES, ALL_NOT_ENEMIES, ALL_NOT_ALLIES):
        total = sum(value for party in values.values() for group in party.values() for value in group.values())
        return {(command, using, 0, 0, 0): total}
    if reach == RANDOM_PARTY:  # one party at random: the average of what each would bring
        totals = [sum(value for group in groups.values() for value in group.values()) for groups in values.values()]
        return {(command, using, 0, 0, 0): sum(totals) / len(totals) if totals else 0.0}
    if reach == PARTY:
        return {
            (command, using, party, 0, 0): sum(value for group in groups.values() for value in group.values())
            for party, groups in values.items()
        }
    if reach == GROUP:
        return {
            (command, using, party, group, 0): sum(group_values.values())
            for party, groups in values.items()
            for group, group_values in groups.items()
        }
    return {  # one target, and ``reach`` neighbours either side of it
        (command, using, party, group, char): sum(
            value for other, value in group_values.items() if char - reach <= other <= char + reach
        )
        for party, groups in values.items()
        for group, group_values in groups.items()
        for char in group_values
    }


AIM_KEEP_PERCENT = {
    Aim.NORMAL: 50,
    Aim.GROUP: 50,  # when there is no leader to follow
    Aim.SMART: 25,
    Aim.TEAM: 25,  # when there is no leader to follow
    Aim.WISE: 10,
    Aim.OMNIPOTENT: 1,
}


def cut(targets: dict[Key, float], percent: int) -> dict[Key, float]:
    """Keep the first ``percent`` of the list, at least one."""
    keep = max(1, math.ceil(len(targets) * percent / 100.0))
    return dict(list(targets.items())[:keep])


def _leader(rules: Rules, fight: Fight, address: Address) -> Combatant | None:
    """The first living fighter before this one in its group (the one a group or a team follows)."""
    party, group, character = address
    for index in sorted(fight.parties[party].groups[group].characters, reverse=True):
        if index < character:
            leader = fight.get((party, group, index))
            if leader.alive(rules):
                return leader
    return None


def narrow(rules: Rules, fight: Fight, address: Address, fighter: Combatant, targets: dict[Key, float]) -> dict[Key, float]:
    """Cut a ranked list down to what a fighter of this ``ai_target`` level would consider."""
    aim = fighter.ai_target
    if aim == Aim.STUPID:
        return targets
    if aim == Aim.VULTURE:
        _, enemies = rules.alignment(fight, address[0])
        vital = rules.vital
        dying = {}
        for key, value in targets.items():
            if key[2] in enemies:
                target = fight.get(key[2:])
                if target.current[vital] > 0 and target.current[vital] / target.get_base(rules, vital) <= 0.1:
                    dying[key] = value
        return dying or targets
    if aim in (Aim.GROUP, Aim.TEAM):
        leader = _leader(rules, fight, address)
        if leader is not None:
            return {(leader.command, leader.using, *leader.target): 0}
    return cut(targets, AIM_KEEP_PERCENT.get(aim, 100))


def select(
    rules: Rules, fight: Fight, address: Address, fighter: Combatant, targets: dict[Key, float], rng: random.Random
) -> Key | None:
    """Rank the list, narrow it by target level, and pick one of the best at random."""
    if not targets:
        return None
    ranked = dict(sorted(targets.items(), key=lambda item: -item[1]))
    ranked = narrow(rules, fight, address, fighter, ranked)
    best = next(iter(ranked.values()))
    return rng.choice([key for key, value in ranked.items() if value == best])


# --- Actions: what each kind of player tries ----------------------------------------------------------


def _affordable(rules: Rules, fighter: Combatant, ability: AbilitySpec) -> bool:
    resource, amount = rules.ability_cost(ability)
    return amount <= fighter.current[resource]


def _ability_command(ability: AbilitySpec) -> int:
    return Command.SPELL if ability.kind == "spell" else Command.SKILL


def _attack_options(fighter: Combatant) -> list[Option]:
    options = [(Command.ATTACK_LEFT, 0)]
    if not fighter.hands_share_weapon():
        options.append((Command.ATTACK_RIGHT, 0))
    return options


def _attacks_and_abilities(rules: Rules, fighter: Combatant, *, need_mp: bool) -> list[Option]:
    options = _attack_options(fighter)
    for index, ability in enumerate(fighter.abilities):
        if not need_mp or _affordable(rules, fighter, ability):
            options.append((_ability_command(ability), index))
    return options


def _items_with(fighter: Combatant, wanted) -> list[Option]:
    return [
        (Command.ITEM, index)
        for index, (item, qty) in enumerate(fighter.inventory)
        if qty > 0 and wanted(item.use_effect.effect)
    ]


def _healing_options(rules: Rules, fighter: Combatant) -> list[Option]:
    options = _items_with(fighter, lambda effect: effect == HEAL)
    options += [
        (_ability_command(ability), index)
        for index, ability in enumerate(fighter.abilities)
        if ability.effect.effect == HEAL and _affordable(rules, fighter, ability)
    ]
    return options


def _damaging_options(rules: Rules, fighter: Combatant) -> list[Option]:
    options = [
        (_ability_command(ability), index)
        for index, ability in enumerate(fighter.abilities)
        if ability.effect.effect == HURT and _affordable(rules, fighter, ability)
    ]
    return options + _items_with(fighter, lambda effect: effect == HURT)


def _spell_skill_or_item_options(rules: Rules, fighter: Combatant) -> list[Option]:
    for kind in ("spell", "skill"):
        options = [
            (_ability_command(ability), index)
            for index, ability in enumerate(fighter.abilities)
            if ability.kind == kind and _affordable(rules, fighter, ability)
        ]
        if options:
            return options
    return _items_with(fighter, lambda effect: effect != NONE)


class _Turn:
    """One fighter choosing: everything an action needs to look at."""

    def __init__(self, rules: Rules, fight: Fight, address: Address, rng: random.Random):
        self.rules, self.fight, self.address, self.rng = rules, fight, address, rng
        self.fighter = fight.get(address)

    def finish(self, option: Option, *, only: Sequence[Address] | None = None, cut_percent: int | None = None) -> Choice | None:
        command, using = option
        values = command_values(self.rules, self.fight, self.fighter, self.address, command, using, self.rng, only)
        effect = command_effect(self.rules, self.fighter, command, using)
        reach = 0 if effect is None or command in NO_TARGET_COMMANDS else effect.targets
        targets = combine_on_reach(command, using, reach, values)
        if cut_percent is not None and targets:
            targets = cut(targets, cut_percent)
        key = select(self.rules, self.fight, self.address, self.fighter, targets, self.rng)
        return None if key is None else Choice(key[0], key[1], key[2:])


def _normal(turn: _Turn, *, cut_percent: int | None = None) -> Choice | None:
    weak = weak_links(turn.rules, turn.fight, turn.address[0], 10)
    healing = _healing_options(turn.rules, turn.fighter)
    if weak and healing:
        return turn.finish(turn.rng.choice(healing), only=weak, cut_percent=cut_percent)
    return turn.finish(turn.rng.choice(_attacks_and_abilities(turn.rules, turn.fighter, need_mp=True)), cut_percent=cut_percent)


def _stupid(turn: _Turn) -> Choice | None:
    return turn.finish(turn.rng.choice(_attacks_and_abilities(turn.rules, turn.fighter, need_mp=False)))


def _healer(turn: _Turn) -> Choice | None:
    healing = _healing_options(turn.rules, turn.fighter)
    if not healing:
        return _normal(turn)
    party = turn.address[0]
    weak = weak_links(turn.rules, turn.fight, party, 50)
    allies, _ = turn.rules.alignment(turn.fight, party)
    for ally in sorted(allies):
        weak += weak_links(turn.rules, turn.fight, ally, 10)
    if not weak:
        return _normal(turn)
    return turn.finish(turn.rng.choice(healing), only=weak)


def _pummeler(turn: _Turn) -> Choice | None:
    options = _damaging_options(turn.rules, turn.fighter) or _attacks_and_abilities(turn.rules, turn.fighter, need_mp=True)
    return turn.finish(turn.rng.choice(options))


def _fighter(turn: _Turn) -> Choice | None:
    return _normal(turn, cut_percent=50)


def _caster(turn: _Turn) -> Choice | None:
    options = _spell_skill_or_item_options(turn.rules, turn.fighter) or _attacks_and_abilities(turn.rules, turn.fighter, need_mp=True)
    return turn.finish(turn.rng.choice(options))


def _mage(turn: _Turn) -> Choice | None:
    options = _spell_skill_or_item_options(turn.rules, turn.fighter) or _attacks_and_abilities(turn.rules, turn.fighter, need_mp=False)
    return turn.finish(turn.rng.choice(options), cut_percent=50)


def _sharp(turn: _Turn) -> Choice | None:
    if turn.rng.randint(1, 100) <= 50:
        return _fighter(turn)
    return _mage(turn)


ACTIONS = {
    Action.STUPID: _stupid,
    Action.NORMAL: _normal,
    Action.HEALER: _healer,
    Action.PROTECTOR: _healer,
    Action.PUMMELER: _pummeler,
    Action.FIGHTER: _fighter,
    Action.HINDERER: _fighter,
    Action.CASTER: _caster,
    Action.MAGE: _mage,
    Action.SHARP: _sharp,
    Action.SMART: _sharp,
    Action.OMNIPOTENT: _sharp,
}


def choose_command(rules: Rules, fight: Fight, address: Address, rng: random.Random) -> Choice | None:
    """The command the fighter at ``address`` commits this round (None: nothing it can do)."""
    play = ACTIONS.get(fight.get(address).ai_action)
    return None if play is None else play(_Turn(rules, fight, address, rng))


def commit(fighter: Combatant, choice: Choice | None) -> None:
    """Gives the fighter the command it chose (it defends if there was none)."""
    if choice is None:
        fighter.command, fighter.using, fighter.target = Command.DEFEND, 0, (0, 0, 0)
    else:
        fighter.command, fighter.using, fighter.target = choice.command, choice.using, choice.target
