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

from .events import EventType, event
from .gear import round_half_up
from .specs import HEAL, RESTORE_MP, REVIVE, AbilitySpec, EffectSpec, ItemSpec


@dataclass(frozen=True)
class AskPlayer:
    """What ``Rules.relation_moved`` returns to ask the owner of the actor's team, rather than change a relationship
    itself: whether to change their team's view of the other by $delta (negative to sour it), and why ($reason)."""

    delta: int
    reason: str = ""


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
    #: How many heroes a party holds, and how many stand in each group of a fight's side built from it: DragonStar's
    #: four groups of five. A party is a collection of whole teams (see ``terraforma.parties``).
    party_size: int = 20
    group_size: int = 5
    #: How long a round waits for the players' commands, in seconds, before it plays with what it has (anyone who
    #: has not committed defends). A round also plays at once when every player's fighter has committed.
    round_seconds: int = 30
    #: The longer rounds a player may choose (an accessibility setting) and what each costs: ``(multiplier, bonus)``,
    #: where $bonus is the share of extra max HP the monsters a player faces get (0.05 is 5%, and they are worth
    #: more experience for it). DragonStar offered 1, 1.5 and 2 for 0, 5 and 10 percent.
    time_multipliers: tuple[tuple[float, float], ...] = ((1.0, 0.0), (1.5, 0.05), (2.0, 0.10))
    #: A drop's chance is out of this (10000: one in ten thousand is the rarest, 10000 is a sure thing).
    drop_chance_scale: int = 10000
    #: Whether the area's drop tables (the map's) roll once for the fight (the default) or for every monster that dies.
    map_drops_once_per_fight: bool = True
    #: How many stacks a hero's inventory holds and how many of one thing a stack holds (``heroes.inventory`` keeps to the
    #: same numbers: a test says so).
    inventory_stacks: int = 12
    stack_size: int = 250
    #: What an empty hand attacks with.
    unarmed: ItemSpec = ItemSpec(key="", name="Fists")

    def round_length(self, multiplier: float = 1.0) -> int:
        """How long a round waits, in seconds, for a fight whose longest-asked multiplier is $multiplier."""
        return math.ceil(self.round_seconds * max(1.0, multiplier))

    def time_bonus(self, multiplier: float) -> float:
        """The share of extra max HP monsters get against a player who chose $multiplier (0 for one that is not offered)."""
        return dict(self.time_multipliers).get(multiplier, 0.0)

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

    def random_party_pool(self, fight, actor_party: int) -> list[int]:
        """The parties a ``random_party`` effect (a seed target scope) may land on, in the fight's order: one of them
        is drawn from the fight's stream. The default is every party with someone still alive, the actor's own
        included (a wild spell does not care whose side it is on). A game narrows it: ``[p for p in super()... if p
        not in allies]`` for a curse that spares friends, say."""
        return [index for index, party in fight.parties.items() if not party.dead(self)]

    def rest(self, vitals: dict[str, int], maximums: dict[str, int]) -> dict[str, int]:
        """What a hero's resources stand at after it rests (an inn, a camp, a revive between fights): $vitals is where
        they stand now (the vital resource at 0 means the hero is dead) and $maximums the most each can hold; returns
        the new values by resource name. This is the game's regeneration and revive rule, applied when the game calls
        ``heroes.service.rest_hero``; the engine never rests a hero on its own. The default fills everything,
        the living and the dead alike. A game might bring the dead back at 1 HP, at half, or heal a part of the maximum:

            def rest(self, vitals, maximums):
                if vitals[self.vital] <= 0:
                    return {**vitals, self.vital: max(1, maximums[self.vital] // 2)}
                return vitals
        """
        return dict(maximums)

    def field_use(self, rng: random.Random, effect: EffectSpec, vitals: dict[str, int], maximums: dict[str, int]) -> dict[str, int] | None:
        """What using an item on a hero outside a fight does (``heroes.field.use_item``): $vitals is where its resources
        stand and $maximums the most each can hold; returns the new values by resource name, or None when the item
        can't be used this way (only healing, restoring mana and reviving are, by default). The default is what the same
        effect does in a fight: heal and restore mana only the living, and a revive fills the life of the dead. A game
        overrides this to allow more (a camp tonic that cures a status) or to forbid some (no revives in the field)."""
        vital, mana = self.vital, self.mana
        alive = vitals.get(vital, 0) > 0
        if effect.effect == HEAL and alive:
            return {**vitals, vital: min(maximums[vital], vitals[vital] + self.roll_amount(rng, effect))}
        if effect.effect == RESTORE_MP and alive and mana in maximums:
            return {**vitals, mana: min(maximums[mana], vitals.get(mana, 0) + self.roll_amount(rng, effect))}
        if effect.effect == REVIVE and not alive:
            return {**vitals, vital: maximums[vital]}
        if effect.effect in (HEAL, RESTORE_MP, REVIVE):
            return dict(vitals)  # a use that does nothing: the caller refuses it
        return None

    def revive_chance(self, rng: random.Random, effect: EffectSpec) -> bool:
        """Whether a revive on someone still alive takes: $base is the chance out of 100."""
        return rng.randint(1, 100) < effect.base

    def revive_amount(self, maximum: int, effect: EffectSpec) -> int:
        """Life given back by a revive that took. DragonStar computes ``round(max * 100 / added)``, kept as it is
        (it reads like a mix-up with ``max * added / 100``; override this if your game wants the percentage)."""
        return round_half_up(maximum * 100.0 / max(effect.added, 1))

    def slay_chance(self, rng: random.Random, effect: EffectSpec) -> bool:
        """Whether a slay takes: $base is the chance out of 100."""
        return rng.randint(1, 100) <= effect.base

    # --- stat values -------------------------------------------------------------------------------------
    def stat_value(self, stat: str, geared: int, bonus: int) -> int:
        """What a stat that is not a resource is worth right now: $geared is its base with the worn gear counted and $bonus what the
        fighter's statuses add (``status.stat_bonus``). The sum, and never below zero; override it to clamp another way."""
        return max(0, geared + bonus)

    # --- statuses --------------------------------------------------------------------------------------
    def status_acted(self, fight, source: tuple, target: tuple, status, intensity: float, ratio: float) -> list:
        """Called when a status token does something that moves no gauge (a lost turn, say; a tick that damages already
        reaches ``gauge_moved``, with the token's source as the actor). $source is who placed the token, $target who
        bears it, $intensity how strong the token was, and $ratio the share of the bearer's worth the source has earned:
        the status's ``xp_share`` times the intensity.

        By default the bearer owes the source that share of its PXP, as it would for harm done to its life: harm for a
        bad status, help for a good one (a negative ratio), recorded like any other debt (``XpDebt``) and paid out when
        the fight ends. Nothing when the ratio is 0."""
        if ratio <= 0:
            return []
        owed = [*source, ratio if status.kind == "bad" else -ratio, self.pxp(fight.get(target))]
        fight.get(target).xp_debts.append(list(owed))
        return [event(EventType.XP_DEBT, *target, *owed)]

    def relation_moved(self, fight, actor: tuple, target: tuple, resource: str, before: int, after: int, maximum: int) -> "int | AskPlayer":
        """Called, beside ``gauge_moved``, when a fighter moves the gauge of one on another team whose party is not an
        enemy of its own (an ally, a neutral, or a partymate): returns how much the actor's team's view of the target's team changes (0 for
        none, negative to sour it: harm to a friend, a spell that caught a bystander; positive to warm it: help). The
        change is a ``RelationChange`` event, applied to the teams' relationship (``Game(relations=...)``) once the fight
        is saved (``fights.store.apply_results``). Or return ``AskPlayer(delta, reason)`` to let the owner of the actor's
        team decide: a ``RelationPrompt`` event says the player is to be asked whether to change their view by $delta,
        and nothing moves until they answer. Both teams must exist. The default changes nothing: relationships move
        only when a game says so, for example

            def relation_moved(self, fight, actor, target, resource, before, after, maximum):
                return -max(1, (before - after) * 10 // maximum) if after < before else 0
        """
        return 0

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

    def pxp(self, fighter) -> int:
        """How strong a fighter is, as one number (its potential experience): see ``fights.potential``."""
        from .potential import potential  # here, since potential reads Rules

        return potential(self, fighter)

    #: How much of the attacker's party PXP the target's party must have for a PvP fight to be allowed: DragonStar-style
    #: 0.85. A stronger target is always allowed.
    pvp_window: float = 0.85

    #: The longest route (steps) a party may be given in one go, however far the client asked it to go.
    route_limit: int = 100

    def party_blocks(self, mover, other) -> bool:
        """Whether the party $other (a ``Party`` row) standing on a tile keeps the party $mover from stepping onto it.
        None of them do, by default: parties walk through each other. Called while a route is made and again at each
        step; a game that wants collisions (or only hostile ones) overrides it."""
        return False

    def may_start_pvp(self, attacker_pxp: int, target_pxp: int, allowed: bool) -> str | None:
        """Whether a party with $attacker_pxp may pick a fight with one of $target_pxp, in a place where PvP is
        $allowed (``PvpZones.allows_pvp``): None if it may, otherwise why not. Refused where PvP is not allowed; where it
        is, allowed against any party at least ``pvp_window`` as strong as the attacker, and always against a stronger one."""
        if not allowed:
            return "Fighting other parties is not allowed here."
        if target_pxp >= attacker_pxp or target_pxp >= attacker_pxp * self.pvp_window:
            return None
        return "That party is too weak to fight."

    def roll_drops(self, fight, rng) -> list:
        """Called when a fight ends, after the experience and the gold: rolls the drops of the monsters that died and
        gives them out (``fights.drops``). Returns the events, already applied to the fight."""
        from .drops import settle  # here, since drops reads Rules

        return settle(self, fight, rng)

    def challenge_earned(self, fight, address) -> int:
        """Called for each hero's fighter when a fight ends: how many Challenge Tokens (the game's special currency, one
        balance per account, kept in ``terraforma.challenge``) it earns. None by default: a game opts in, and this is where
        it sets its earning rates. Called only for heroes, so an admin earns only when fighting as a player, and never for a
        monster or a fight they only watched. A negative answer pays nothing. What is paid is then cut to fit
        ``challenge_daily_cap`` and ``challenge_purse_cap``."""
        return 0

    def challenge_daily_cap(self, account_id: int) -> int | None:
        """The most Challenge Tokens an account may earn from fights in one UTC day, or None for no limit (the default).
        What would go over is not paid. It does not limit purchases or spending."""
        return None

    def challenge_purse_cap(self, account_id: int) -> int | None:
        """The most Challenge Tokens an account may hold, or None for no limit (the default). Earnings are cut to fit; a
        purchase that would not fit is refused whole."""
        return None

    def drop_recipients(self, fight, party: int, monsters, share: str, rng) -> list:
        """Who in $party receives one drop of the monsters that died ($monsters: the one that dropped it, or all of them for
        an area's table), given its $share (``one``, ``each_team`` or ``each_member``). Returns the fighters' addresses.
        By default one at random among the heroes who contributed to the kill (struck it, harmed it through a status, placed
        a bad status on it, or buffed one who did; healing does not count), every one with an equal chance."""
        from .drops import default_recipients

        return default_recipients(fight, party, monsters, share, rng)

    def drop_mode(self, fight, party: int, monsters, share: str, rng) -> str:
        """How one drop is given out in $party: ``"auto"`` (the default: ``drop_recipients`` picks at once), ``"need_want"``
        (held until every hero has said need, want or pass; the highest roll wins, need before want) or ``"assign"`` (held
        until someone ``may_assign_drop`` chooses who gets it). Held drops become pending drops (``fights.pending``)."""
        from .drops import AUTO

        return AUTO

    def may_assign_drop(self, hero_ids, hero_id: int, is_leader: bool = False) -> bool:
        """Whether $hero_id may choose who gets a held ``"assign"`` drop of a party whose heroes are $hero_ids. By default the
        party's leader may (``is_leader``: the hero's player founded the party or accepted another into theirs, see
        ``parties.service``) and nobody else; a game says otherwise here."""
        return is_leader

    def status_worth(self, statuses: dict, target, effect) -> float | None:
        """What a status or stat effect is worth to the monster AI aiming it at $target, in hit points (zero or more);
        None for any other effect. See ``fights.ai_status`` for how it is reckoned, and override this for your own way."""
        from .ai_status import effect_worth  # here, since ai_status reads Rules

        return effect_worth(self, statuses, target, effect)

    # --- hooks ---------------------------------------------------------------------------------
    def gauge_moved(self, fight, actor: tuple, target: tuple, resource: str, before: int, after: int, maximum: int) -> list:
        """Called whenever one fighter moves another's resource (damage, healing, restoring): the place for
        experience debts and the like. Returns events to add after the one that moved it.

        By default the target now owes the actor experience for what it did to its life or mana: the share of the
        gauge that moved (positive for harm, negative for help) at the target's own PXP. ``fights.experience`` pays
        the debts out when the fight ends. Like the other changes of a round it changes the fight as well as saying so."""
        if resource not in (self.vital, self.mana) or before == after or maximum <= 0:
            return []
        owed = [*actor, (before - after) / maximum, self.pxp(fight.get(target))]
        fight.get(target).xp_debts.append(list(owed))
        return [event(EventType.XP_DEBT, *target, *owed)]

    # --- the end of a fight: experience, gold and advancement ---------------------------------------
    #: The highest level; a fighter there needs no more experience.
    max_level: int = 100

    def fight_is_over(self, fight) -> bool:
        """Whether the fight has ended: one party or none is left standing, or those left are all allies."""
        live = [index for index, party in fight.parties.items() if not party.dead(self)]
        return len(live) < 2 or all(set(live) <= self.alignment(fight, index)[0] for index in live)

    def on_fight_end(self, fight, rng) -> list:
        """Called when ``fight_is_over`` first holds: pays out experience and gold and advances whoever earned
        enough (``fights.rewards``). Returns the events and has already applied them to the fight. A game overrides
        this to reward differently (stat bonuses for performance, an experience currency, ...)."""
        from .rewards import settle  # here, since rewards reads Rules

        return settle(self, fight, rng)

    def experience_needed(self, level: int, job_need: int) -> int:
        """The experience that takes a fighter of ``level`` up to the next: the job's ``xp_needed`` at level 1, and
        each level after that adds half of it times (level + 3) * (level + 2) / 2 - 1."""
        need = job_need
        for each in range(1, level):
            need += round_half_up(job_need / 2.0 * (((each + 3) * (each + 2) / 2) - 1))
        return need

    def advance(self, fight, address: tuple, rng) -> list:
        """Level-ups for the fighter at ``address`` after it earned experience: one ``LevelUp`` event, with the
        stat gains, for each level it has the experience for. A job's growth per level is ``growth``; each stat grows
        by 75 to 100 percent of it (rounded down), drawn from the fight's stream. Does nothing for a fighter with
        no job (``job_need`` 0). The fight is changed by the caller, as for every event."""
        fighter = fight.get(address)
        events = []
        if fighter.job_need <= 0:
            return events
        level = fighter.level
        while level < self.max_level and fighter.exp >= self.experience_needed(level, fighter.job_need):
            gains = {}
            for stat in self.stats:
                if stat in fighter.growth:
                    gain = math.floor(fighter.growth[stat] * (75 + rng.randint(0, 25)) / 100.0)
                    if gain:
                        gains[stat] = gain
            level += 1
            events.append(event(EventType.LEVEL_UP, *address, level, gains))
        return events

    def after_event(self, fight, event) -> list:
        """Called for every event a fight produces, to let a game react (fill a rage meter, grant experience);
        returns more events to add after it. Nothing by default."""
        return []
