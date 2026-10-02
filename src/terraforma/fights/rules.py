"""The rules of a fight, as one class a game can override.

``Rules`` holds every formula and every list a fight uses: the stats, the
resources, how fast someone acts, the chance to hit, how much damage a hit
does, the saving throw, and what each effect does. ``Rules()`` is
DragonStar's rules, unchanged; a game overrides what it wants:

    class MyRules(Rules):
        stats = (*Rules.stats, "Luck")

        def chance_to_hit(self, rng, accuracy, dodge):
            ...

This is a public interface (the license exception covers it): the names
and signatures below are what games build on, so change them deliberately.
Every method is pure apart from the random stream it is handed (a
``random.Random`` from ``WorldRng``), so a fight replays exactly.

Resources are the stats that are pools: ``base`` holds the maximum and
``current`` the amount left (HP and MP). The ``vital`` one is life: a
fighter at zero is dead. A game can list other resources (rage,
technique points); how they fill and drain is up to its overrides of
``ability_cost`` and ``after_event``.
"""

import math
import random
from dataclasses import dataclass

from .gear import round_half_up
from .specs import AbilitySpec, EffectSpec, ItemSpec


@dataclass(frozen=True)
class Resource:
    name: str
    #: Zero of this resource is death.
    vital: bool = False


class Rules:
    # --- what a fighter is made of ---------------------------------------------------
    stats: tuple[str, ...] = ("HP", "MP", "Speed", "Accuracy", "Strength", "Dodge", "Block", "Power", "Resistance", "Focus")
    resources: tuple[Resource, ...] = (Resource("HP", vital=True), Resource("MP"))
    #: Stats a hand's gear counts for only when that hand acts.
    hand_stats: tuple[str, ...] = ("Strength", "Accuracy", "Speed")
    #: The resource spells cost and RESTORE_MP refills.
    mana: str = "MP"
    #: What an empty hand attacks with.
    unarmed: ItemSpec = ItemSpec(key="", name="Fists")

    @property
    def vital(self) -> str:
        return next(resource.name for resource in self.resources if resource.vital)

    @property
    def resource_names(self) -> tuple[str, ...]:
        return tuple(resource.name for resource in self.resources)

    # --- order ------------------------------------------------------------------------
    def randomize(self, rng: random.Random, value: int, deviation: int = 15) -> int:
        """$value spread up or down by up to $deviation percent."""
        return math.floor(rng.randint(100 - deviation, 100 + deviation) * value / 100.0)

    def speed(self, rng: random.Random, speed: int, casting: AbilitySpec | None = None, focus: int = 0) -> int:
        """How fast someone acts this round. Casting slows them, less the more Focus they have against the spell's cost."""
        value = self.randomize(rng, speed)
        if casting is not None:
            focus = max(0, self.randomize(rng, focus))
            summoned = math.sqrt(focus)
            penalty = 1 / (summoned / (casting.mp_cost + 1) + 2)
            value -= round_half_up(value * penalty)
        return value

    # --- hitting ------------------------------------------------------------------------
    def hit_chance(self, accuracy: int, dodge: int) -> float:
        """The chance, in percent, that an attack lands: Accuracy out of Accuracy plus Dodge (each at least 1)."""
        accuracy, dodge = max(accuracy, 1), max(dodge, 1)
        return 100.0 * accuracy / (accuracy + dodge)

    def chance_to_hit(self, rng: random.Random, accuracy: int, dodge: int) -> int | None:
        """Rolls out of 100 against the hit chance. None for a miss; for a hit, the roll (1 to 100): the lower, the
        cleaner the hit. The roll is always out of 100, so how often it takes a given value doesn't depend on
        how large Accuracy and Dodge are (which keeps criticals as common at high levels as at low ones)."""
        roll = rng.randint(1, 100)
        return roll if roll <= self.hit_chance(accuracy, dodge) else None

    def is_critical(self, roll: int) -> bool:
        """Whether a hit is a critical. By default only the best possible roll, a 1: one hit in a hundred, always."""
        return roll == 1

    def hit_damage(self, strength: int, block: int, window: float, roll: int, defending: bool, impact: float,
                   critical: bool = False) -> int:
        """Damage from a hit that landed (never less than 1). $window is the hit chance in percent, so $roll (1 up to
        the window) says how cleanly it hit: from half damage at the edge of the window to full at a 1. Strength
        doubles on a critical, Block blunts it, and defending halves it."""
        window, strength, block = max(window, 1.0), max(strength, 1), max(block, 1)
        if critical:
            strength *= 2
        ratio = math.atan2(strength, block) * 2.0 / math.pi
        damage = math.floor(strength * ratio * impact * (0.5 + ((window + 1 - roll) / window) * 0.5))
        if defending:
            damage = math.floor(damage / 2)
        return max(damage, 1)

    def saving_throw(self, rng: random.Random, power: int, resistance: int) -> float:
        """The share of a harmful spell that gets through: 1.0 (all), 0.5 (half) or 0.0 (none)."""
        roll = rng.randint(1, max(power + resistance, 1))
        if roll > power + resistance / 2:
            return 0.0
        if roll > power:
            return 0.5
        return 1.0

    # --- costs and effects -----------------------------------------------------------------
    def ability_cost(self, ability: AbilitySpec) -> tuple[str, int]:
        """What using an ability costs: (resource, amount)."""
        return self.mana, ability.mp_cost

    def weapon_effect(self, weapon: ItemSpec, strength: int) -> EffectSpec:
        return weapon.weapon_effect(strength)

    def roll_amount(self, rng: random.Random, effect: EffectSpec) -> int:
        """The base amount of an effect plus a random share of the extra."""
        return rng.randint(0, effect.added) + effect.base

    def revive_chance(self, rng: random.Random, effect: EffectSpec) -> bool:
        """Whether a revive on someone still alive takes: $base is the chance out of 100."""
        return rng.randint(1, 100) < effect.base

    def revive_amount(self, maximum: int, effect: EffectSpec) -> int:
        """Life given back by a revive that took. DragonStar computes ``round(max * 100 / added)``, kept as it is
        (it reads like a mix-up with ``max * added / 100``; override this if your game wants the percentage)."""
        return round_half_up(maximum * 100.0 / max(effect.added, 1))

    def alignment(self, fight, party: int) -> tuple[set[int], set[int]]:
        """How the fight's other parties stand to $party: (allies, enemies). A party in neither is neutral to it.
        A party is always its own ally. Reads each party's ``allies`` and ``enemies`` if the fight set them: with
        only the allies set, everyone else is an enemy; with only the enemies set, only the party itself is an ally,
        and the rest are neutral. With neither set, as in DragonStar's simple case, each party is for itself:
        no allies, every other party an enemy. Override it for alliances, factions and neutrals of your own."""
        mine = fight.parties[party]
        everyone = set(fight.parties)
        allies = {party} | (set(mine.allies) if mine.allies is not None else set())
        enemies = set(mine.enemies) if mine.enemies is not None else everyone - allies
        return allies, enemies - allies

    # --- hooks ---------------------------------------------------------------------------------
    def gauge_moved(self, fight, actor: tuple, target: tuple, resource: str, before: int, after: int, maximum: int) -> list:
        """Called whenever one fighter moves another's resource (damage, healing, restoring): the place for
        experience debts and the like. Returns events to add after the one that moved it. Nothing by default."""
        return []

    def after_event(self, fight, event) -> list:
        """Called for every event a fight produces, to let a game react (fill a rage meter, grant experience);
        returns more events to add after it. Nothing by default."""
        return []
