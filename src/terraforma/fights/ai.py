"""How the computer plays a fighter: monsters, NPCs and heroes on autopilot.

Pure functions, no database or web code. They carry DragonStar's AI over as the
engine's defaults ("the process mirrored, functionally equivalent"). A fighter's
numbers come from its ``ai`` block in the seed (``MonsterAi``: action, goal,
target, experience); what they mean is defined here, and a game's own fight
rules may replace the whole thing.

Every random choice draws from the ``random.Random`` the caller passes in, which
is the fight's own ``WorldRng`` stream, so a fight replays exactly.

A command is chosen in four steps, as in DragonStar:

1. the *action* (what kind of player it is) picks a command to try: an attack
   with a hand, an item, a skill or a spell;
2. the *goal* (what it wants) values that command on every fighter, positive
   for the ones it wants to affect and negative for the rest;
3. those values are added up by how far the command reaches (one target, a
   group, a party, everyone);
4. the *target* level (how well it judges) keeps only the best share of the
   list, and one of the best is picked at random.

Fighters are addressed by ``(party, group, character)``, as ``FighterRef`` does.
The snapshots below are what 5c's fight state is turned into for the AI to read.

Kept on purpose, as DragonStar has them: ``revive`` is worth a negative number
against a dead ally, and ``restore_mp`` is worth the square of what it restores.
"""

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum

from terraforma.fights.experience import round_half_up

Address = tuple[int, int, int]


class Command(IntEnum):
    """The numbers DragonStar's commands have."""

    ATTACK_LEFT = 0
    ATTACK_RIGHT = 1
    ITEM = 2
    EQUIP = 3
    SKILL = 4
    SPELL = 5
    DEFEND = 6
    FLEE = 7
    EQUIP_WEAPON_AND_AMMO = 8


class Action(IntEnum):
    """How a fighter plays: the ``ai.action`` number."""

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
    """What a fighter wants: the ``ai.goal`` number."""

    RANDOM = 0
    DESTRUCTOR = 1
    SCHEMER = 2
    PREVENTOR = 3
    PROTECTOR = 4


class Aim(IntEnum):
    """How well a fighter judges its target: the ``ai.target`` number."""

    STUPID = 0
    VULTURE = 1
    NORMAL = 2
    GROUP = 3
    SMART = 4
    TEAM = 5
    WISE = 6
    OMNIPOTENT = 7


# Effects that harm their target, and effects that need the target to be alive.
DETRIMENTAL = frozenset({"hurt", "slay", "decrease_stats", "steal_stats", "remove_good_status", "cause_bad_status"})
LIVING_ONLY = frozenset(
    {
        "heal", "hurt", "slay", "increase_stats", "decrease_stats", "steal_stats", "cause_good_status",
        "remove_good_status", "cause_bad_status", "remove_bad_status", "restore_mp",
    }
)  # fmt: skip
USABLE_IN_COMBAT = LIVING_ONLY | {"revive"}


@dataclass(frozen=True)
class Usable:
    """An ability or an item as the AI sees it: what it does, to whom, and what it costs."""

    effect: str = "none"
    targets: str = "individual"
    base: int = 0
    added: int = 0
    mp_cost: int = 0
    spell: bool = False  # abilities only: a spell, not a skill


@dataclass(frozen=True)
class Slot:
    """An inventory slot the AI may use an item from."""

    item: Usable
    qty: int = 1


@dataclass(frozen=True)
class Committed:
    """What a fighter has already chosen this round (a group follows its first living member)."""

    command: int
    using: int
    target: Address


@dataclass(frozen=True)
class AiFighter:
    address: Address
    hp: int
    max_hp: int
    mp: int
    pxp: int
    stats: Mapping[str, int]
    action: int = Action.STUPID
    goal: int = Goal.RANDOM
    target: int = Aim.STUPID
    experience: int = 0
    abilities: Sequence[Usable] = ()
    inventory: Sequence[Slot] = ()
    hands_share_weapon: bool = False
    #: What each hand's attack does and reaches (left, right).
    attack_effects: tuple[str, str] = ("hurt", "hurt")
    attack_targets: tuple[str, str] = ("individual", "individual")
    #: Stats that depend on the command (a hand's Strength and Accuracy): (stat, command) -> value.
    command_stats: Mapping[tuple[str, int], int] = None  # type: ignore[assignment]
    committed: Committed | None = None

    @property
    def dead(self) -> bool:
        return self.hp < 1

    def current(self, stat: str, command: int | None = None) -> int:
        if command is not None and self.command_stats and (stat, command) in self.command_stats:
            return self.command_stats[(stat, command)]
        return self.stats[stat]


@dataclass(frozen=True)
class AiParty:
    index: int
    groups: Sequence[Sequence[AiFighter]]
    allies: frozenset[int]
    enemies: frozenset[int]


@dataclass(frozen=True)
class Choice:
    command: int
    using: int
    #: Who or what it is aimed at; (0, 0, 0)-style zeros for what has no single target.
    target: Address


Key = tuple[int, int, int, int, int]  # command, using, target party, group, character
Values = dict[int, dict[int, dict[int, float]]]


# --- Profiles: the numbers a fighter's AI is set to -------------------------------------

LEVEL_JITTER = 3
EXP_RECRUIT, EXP_PRIVATE, EXP_SERGEANT, EXP_LIEUTENANT, EXP_COLONEL, EXP_GENERAL, EXP_OMNIPOTENT = 50, 40, 30, 20, 10, 5, 0


@dataclass(frozen=True)
class Profile:
    action: int
    goal: int
    target: int
    experience: int


def goal_for(action: int) -> int:
    if action == Action.HEALER:
        return Goal.PROTECTOR
    if action == Action.CASTER:
        return Goal.SCHEMER
    return Goal.DESTRUCTOR


def specialty(abilities: Sequence[Usable]) -> int:
    """What a fighter's combat abilities make it best at.

    Healer when 40% or more heal or revive; Mage or Caster when mostly spells
    (Mage if they mostly hurt); Pummeler when mostly hurting skills; else Fighter.
    """
    heal = hurt_spell = other_spell = hurt_skill = other_skill = 0
    for ability in abilities:
        if ability.effect not in USABLE_IN_COMBAT:
            continue
        if ability.effect in ("heal", "revive"):
            heal += 1
        elif ability.effect in ("hurt", "slay"):
            if ability.spell:
                hurt_spell += 1
            else:
                hurt_skill += 1
        elif ability.spell:
            other_spell += 1
        else:
            other_skill += 1
    total = heal + hurt_spell + other_spell + hurt_skill + other_skill
    if total == 0:
        return Action.FIGHTER
    if heal * 5 >= total * 2:
        return Action.HEALER
    if hurt_spell + other_spell > hurt_skill + other_skill:
        return Action.MAGE if hurt_spell >= other_spell else Action.CASTER
    return Action.PUMMELER if hurt_skill > 0 else Action.FIGHTER


def profile_for_level(
    level: int, abilities: Sequence[Usable], *, player: bool, rng: random.Random, jitter: int | None = None
) -> Profile:
    """The AI numbers for a fighter of ``level``, give or take up to LEVEL_JITTER levels.

    Under 5 Stupid; 5-9 Normal; 10-19 its specialty; 20-29 and 30-39 Sharp with
    sharper targeting and experience; 40-49 Smart; 50 and up Omnipotent. A
    player's heroes stop at Sharp, Wise and General experience. ``jitter`` 0
    gives the plain recommendation (for an editor).
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
            min(profile.action, Action.SHARP),
            profile.goal,
            min(profile.target, Aim.WISE),
            max(profile.experience, EXP_GENERAL),
        )
    return profile


# --- Valuing a command on a fighter -----------------------------------------------------


def _usable_for(fighter: AiFighter, command: int, using: int) -> Usable | None:
    if command == Command.ITEM:
        return fighter.inventory[using].item if 0 <= using < len(fighter.inventory) else None
    if command in (Command.SKILL, Command.SPELL):
        return fighter.abilities[using] if 0 <= using < len(fighter.abilities) else None
    return None


def command_effect(fighter: AiFighter, command: int, using: int) -> str | None:
    """What a command does; None when it names something the fighter does not have."""
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return fighter.attack_effects[command]
    if command in (Command.ITEM, Command.SKILL, Command.SPELL):
        usable = _usable_for(fighter, command, using)
        return None if usable is None else usable.effect
    if command in (Command.EQUIP, Command.DEFEND, Command.FLEE, Command.EQUIP_WEAPON_AND_AMMO):
        return "none"
    return None


def command_reach(fighter: AiFighter, command: int, using: int) -> str:
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return fighter.attack_targets[command]
    usable = _usable_for(fighter, command, using)
    return usable.targets if usable else "individual"


def perceived(fighter: AiFighter, target: AiFighter, stat: str, rng: random.Random) -> float:
    """A stat of the target as the fighter reads it: skewed by up to its ``experience`` percent.

    DragonStar's code divides by the random factor and so reads every stat as
    about a hundredth of its value (and skews it the wrong way round); its own
    notes say the intent is a skew of up to +-experience percent, so that is what
    this does.
    """
    spread = fighter.experience * 100
    return target.current(stat) * rng.randint(10000 - spread, 10000 + spread) / 10000


def _attack_damage(fighter: AiFighter, target: AiFighter, command: int, rng: random.Random) -> float:
    strength = fighter.current("Strength", command)
    block = perceived(fighter, target, "Block", rng)
    return max(1, strength - math.floor(block / 2))


def _attack_success(fighter: AiFighter, target: AiFighter, command: int, rng: random.Random) -> float:
    accuracy = fighter.current("Accuracy", command)
    dodge = perceived(fighter, target, "Dodge", rng)
    return accuracy / (accuracy + dodge) if accuracy + dodge else 0.0


def _cast_success(fighter: AiFighter, target: AiFighter, rng: random.Random) -> float:
    power = fighter.current("Power")
    resistance = perceived(fighter, target, "Resistance", rng)
    total = power + resistance
    return power / total + min(power, resistance) / total / 2 if total else 0.0


def _effect_damage(target: AiFighter, usable: Usable) -> float:
    effect = usable.effect
    if effect in ("heal", "hurt"):
        return 0 if target.hp == 0 else usable.added / 2 + usable.base
    if effect == "revive":
        if target.hp == 0:
            return target.hp - target.max_hp
        return round_half_up(target.max_hp * 400.0 / usable.added) if usable.added else 0
    if effect == "restore_mp":
        if target.hp == 0:
            return 0
        amount = usable.added / 2 + usable.base
        return amount * amount
    return 0  # nothing, or an effect the AI does not value yet


def _destructor(fighter: AiFighter, target: AiFighter, command: int, using: int, rng: random.Random) -> float:
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return _attack_damage(fighter, target, command, rng)
    usable = _usable_for(fighter, command, using)
    if usable is None:
        return 0
    return _effect_damage(target, usable)


def _schemer(fighter: AiFighter, target: AiFighter, command: int, using: int, rng: random.Random) -> float:
    if command in (Command.ATTACK_LEFT, Command.ATTACK_RIGHT):
        return _attack_damage(fighter, target, command, rng) * _attack_success(fighter, target, command, rng)
    usable = _usable_for(fighter, command, using)
    if usable is None:
        return 0
    value = _effect_damage(target, usable)
    if command == Command.SKILL:
        return value * _attack_success(fighter, target, command, rng)
    if command == Command.SPELL:
        return value * _cast_success(fighter, target, rng)
    return value


def goal_value(
    fighter: AiFighter,
    target: AiFighter,
    command: int,
    using: int,
    alignment: bool | None,
    effect: str,
    rng: random.Random,
) -> float:
    """What a command is worth against one target: positive to aim at it, negative to avoid it.

    ``alignment`` is True for an ally (or itself), False for an enemy, None for
    a neutral party.
    """
    if effect in LIVING_ONLY and target.dead:
        return 0
    detrimental = effect in DETRIMENTAL
    weight = 1 if (alignment is True and not detrimental) or (alignment is False and detrimental) else -1
    goal = fighter.goal
    if goal == Goal.RANDOM:
        value = rng.randint(1, 1000)
    elif goal == Goal.DESTRUCTOR:
        value = _destructor(fighter, target, command, using, rng)
    elif goal == Goal.SCHEMER:
        value = _schemer(fighter, target, command, using, rng)
    elif goal == Goal.PREVENTOR:
        value = _destructor(fighter, target, command, using, rng)
        value = 0 if target.hp == 0 else math.floor(value / target.hp * 1000)
    elif goal == Goal.PROTECTOR:
        value = target.pxp * target.hp
    else:
        return 0
    return value * weight


# --- The fight as the AI reads it -------------------------------------------------------


class AiFight:
    def __init__(self, parties: Sequence[AiParty]):
        self.parties = {party.index: party for party in parties}

    def fighter(self, address: Address) -> AiFighter:
        party, group, character = address
        return self.parties[party].groups[group][character]

    def alignment(self, party: int, toward: int) -> bool | None:
        if toward == party or toward in self.parties[party].allies:
            return True
        return False if toward in self.parties[party].enemies else None

    def weak_links(self, party: int, percent: int) -> list[Address]:
        """The living fighters of ``party`` at ``percent`` of their HP or less."""
        weak = []
        for group_index, group in enumerate(self.parties[party].groups):
            for char_index, fighter in enumerate(group):
                ratio = fighter.hp * 100 / fighter.max_hp
                if 0 < ratio <= percent:
                    weak.append((party, group_index, char_index))
        return weak

    def values(
        self,
        fighter: AiFighter,
        command: int,
        using: int,
        rng: random.Random,
        only: Sequence[Address] | None = None,
    ) -> Values:
        """The command's worth on every fighter (0 on the ones not in ``only``, when given)."""
        effect = command_effect(fighter, command, using)
        if effect is None:
            return {}
        values: Values = {}
        for party_index, party in self.parties.items():
            alignment = self.alignment(fighter.address[0], party_index)
            for group_index, group in enumerate(party.groups):
                for char_index, target in enumerate(group):
                    address = (party_index, group_index, char_index)
                    if only is not None and address not in only:
                        value = 0
                    else:
                        value = goal_value(fighter, target, command, using, alignment, effect, rng)
                    values.setdefault(party_index, {}).setdefault(group_index, {})[char_index] = value
        return values


def combine_on_reach(command: int, using: int, reach: str, values: Values) -> dict[Key, float]:
    """Add the values up by how far the command reaches: one key per thing it can be aimed at."""
    if reach in ("all_parties", "all_enemies", "all_allies"):
        total = sum(value for party in values.values() for group in party.values() for value in group.values())
        return {(command, using, 0, 0, 0): total}
    if reach == "party":
        return {
            (command, using, party, 0, 0): sum(value for group in groups.values() for value in group.values())
            for party, groups in values.items()
        }
    if reach == "group":
        return {
            (command, using, party, group, 0): sum(group_values.values())
            for party, groups in values.items()
            for group, group_values in groups.items()
        }
    return {
        (command, using, party, group, char): value
        for party, groups in values.items()
        for group, group_values in groups.items()
        for char, value in group_values.items()
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


def _leader(fight: AiFight, fighter: AiFighter) -> Committed | None:
    party, group, character = fighter.address
    for index in range(character - 1, -1, -1):
        leader = fight.parties[party].groups[group][index]
        if not leader.dead and leader.committed is not None:
            return leader.committed
    return None


def narrow(fight: AiFight, fighter: AiFighter, targets: dict[Key, float]) -> dict[Key, float]:
    """Cut a ranked list down to what a fighter of this ``target`` level would consider."""
    aim = fighter.target
    if aim == Aim.STUPID:
        return targets
    if aim == Aim.VULTURE:
        enemies = fight.parties[fighter.address[0]].enemies
        dying = {
            key: value
            for key, value in targets.items()
            if key[2] in enemies
            and (target := fight.fighter(key[2:])).hp > 0
            and target.hp / target.max_hp <= 0.1
        }
        return dying or targets
    if aim in (Aim.GROUP, Aim.TEAM):
        leader = _leader(fight, fighter)
        if leader is not None:
            command_key = (leader.command, leader.using, *leader.target)
            return {command_key: 0}
    return cut(targets, AIM_KEEP_PERCENT.get(aim, 100))


def select(fight: AiFight, fighter: AiFighter, targets: dict[Key, float], rng: random.Random) -> Key | None:
    """Rank the list, narrow it by target level, and pick one of the best at random."""
    if not targets:
        return None
    ranked = dict(sorted(targets.items(), key=lambda item: -item[1]))
    ranked = narrow(fight, fighter, ranked)
    best = next(iter(ranked.values()))
    return rng.choice([key for key, value in ranked.items() if value == best])


# --- Actions: what each kind of player tries --------------------------------------------

Option = tuple[int, int]  # command, using


def _attack_options(fighter: AiFighter) -> list[Option]:
    options = [(Command.ATTACK_LEFT, 0)]
    if not fighter.hands_share_weapon:
        options.append((Command.ATTACK_RIGHT, 0))
    return options


def _ability_command(ability: Usable) -> int:
    return Command.SPELL if ability.spell else Command.SKILL


def _attacks_and_abilities(fighter: AiFighter, *, need_mp: bool) -> list[Option]:
    options = _attack_options(fighter)
    for index, ability in enumerate(fighter.abilities):
        if not need_mp or ability.mp_cost <= fighter.mp:
            options.append((_ability_command(ability), index))
    return options


def _healing_options(fighter: AiFighter) -> list[Option]:
    options = [
        (Command.ITEM, index) for index, slot in enumerate(fighter.inventory) if slot.qty > 0 and slot.item.effect == "heal"
    ]
    options += [
        (_ability_command(ability), index)
        for index, ability in enumerate(fighter.abilities)
        if ability.effect == "heal" and ability.mp_cost <= fighter.mp
    ]
    return options


def _damaging_options(fighter: AiFighter) -> list[Option]:
    options = [
        (_ability_command(ability), index)
        for index, ability in enumerate(fighter.abilities)
        if ability.effect == "hurt" and ability.mp_cost <= fighter.mp
    ]
    options += [
        (Command.ITEM, index) for index, slot in enumerate(fighter.inventory) if slot.qty > 0 and slot.item.effect == "hurt"
    ]
    return options


def _spell_or_skill_or_item_options(fighter: AiFighter) -> list[Option]:
    for wanted in (True, False):
        options = [
            (_ability_command(ability), index)
            for index, ability in enumerate(fighter.abilities)
            if ability.spell is wanted and ability.mp_cost <= fighter.mp
        ]
        if options:
            return options
    return [
        (Command.ITEM, index) for index, slot in enumerate(fighter.inventory) if slot.qty > 0 and slot.item.effect != "none"
    ]


def _finish(
    fight: AiFight,
    fighter: AiFighter,
    option: Option,
    rng: random.Random,
    *,
    only: Sequence[Address] | None = None,
    cut_percent: int | None = None,
) -> Choice | None:
    command, using = option
    values = fight.values(fighter, command, using, rng, only)
    targets = combine_on_reach(command, using, command_reach(fighter, command, using), values)
    if cut_percent is not None and targets:
        targets = cut(targets, cut_percent)
    key = select(fight, fighter, targets, rng)
    return None if key is None else Choice(key[0], key[1], key[2:])


def _normal(fight: AiFight, fighter: AiFighter, rng: random.Random, *, cut_percent: int | None = None) -> Choice | None:
    weak = fight.weak_links(fighter.address[0], 10)
    healing = _healing_options(fighter)
    if weak and healing:
        return _finish(fight, fighter, rng.choice(healing), rng, only=weak, cut_percent=cut_percent)
    options = _attacks_and_abilities(fighter, need_mp=True)
    return _finish(fight, fighter, rng.choice(options), rng, cut_percent=cut_percent)


def _stupid(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    options = _attacks_and_abilities(fighter, need_mp=False)
    return _finish(fight, fighter, rng.choice(options), rng)


def _healer(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    healing = _healing_options(fighter)
    if not healing:
        return _normal(fight, fighter, rng)
    party = fighter.address[0]
    weak = fight.weak_links(party, 50)
    for ally in fight.parties[party].allies:
        weak += fight.weak_links(ally, 10)
    if not weak:
        return _normal(fight, fighter, rng)
    return _finish(fight, fighter, rng.choice(healing), rng, only=weak)


def _pummeler(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    options = _damaging_options(fighter) or _attacks_and_abilities(fighter, need_mp=True)
    return _finish(fight, fighter, rng.choice(options), rng)


def _fighter(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    return _normal(fight, fighter, rng, cut_percent=50)


def _caster(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    options = _spell_or_skill_or_item_options(fighter) or _attacks_and_abilities(fighter, need_mp=True)
    return _finish(fight, fighter, rng.choice(options), rng)


def _mage(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    options = _spell_or_skill_or_item_options(fighter) or _attacks_and_abilities(fighter, need_mp=False)
    return _finish(fight, fighter, rng.choice(options), rng, cut_percent=50)


def _sharp(fight: AiFight, fighter: AiFighter, rng: random.Random) -> Choice | None:
    if rng.randint(1, 100) <= 50:
        return _fighter(fight, fighter, rng)
    return _mage(fight, fighter, rng)


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


def choose_command(parties: Sequence[AiParty], address: Address, rng: random.Random) -> Choice | None:
    """The command the fighter at ``address`` commits this round (None: nothing it can do)."""
    fight = AiFight(parties)
    fighter = fight.fighter(address)
    play = ACTIONS.get(fighter.action)
    return None if play is None else play(fight, fighter, rng)
