"""Statuses: what a fighter can be under, and how it works out in a round.

Pure code, no database or web. A status is a *definition* (``StatusSpec``, from the seed's ``statuses.json``);
a fighter under one carries a *token* (``StatusToken``) that says who placed it, how long it has lasted and
how long it has left. Two mechanisms live side by side:

1. **Status tokens.** A status lists *ticks* (things it does when something happens: the start or end of the
   round, the start or end of its bearer's turn, when the bearer is helped or harmed, when it makes a saving
   throw; each every n-th time if it likes) and *modifiers* (things that hold while it lasts: damage of a kind
   taken or dealt changed, a stat raised or lowered). It may have a duration in rounds, and an intensity that
   follows the time left: flat, rising or falling. Everything it does is scaled by that intensity.
2. **Direct stat adjustment.** Not a status at all: the effects ``increase_stats``, ``decrease_stats`` and
   ``steal_stats`` move the *current* value of a stat (``AlterStat`` events), and every round it drifts back
   towards the base (``Rules.stat_drift``).

Every token records its *source*, the fighter who placed it. Whatever the token does, it does on the source's
behalf: damage from a tick goes through the usual damage path with the source as the actor, so the experience
rules see the source earn it (``Rules.gauge_moved``), and each tick that moves no gauge is reported to
``Rules.status_acted`` with a ratio scaled by the token's intensity, so the source gets its share of what the
afflicted fighter is worth (its PXP) all the same.

Why replays still work: a token's ``rounds`` and ``turns`` counters change only through ``age`` and ``count_turn``,
which the live fight and ``replay.apply_events`` both call, from events that are in the log (``RoundEnd`` and
``Turn``). Nothing here rolls dice except a tick that skips a turn with a chance below 100.
"""

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # combatant.py imports this module, for the tokens it carries
    from .combatant import Combatant

Address = tuple[int, int, int]

# When a tick happens.
ROUND_START = "round_start"
ROUND_END = "round_end"
TURN_START = "turn_start"
TURN_END = "turn_end"
HELPED = "helped"
HARMED = "harmed"
SAVING_THROW = "saving_throw"
WHENS = (ROUND_START, ROUND_END, TURN_START, TURN_END, HELPED, HARMED, SAVING_THROW)

# What a tick does.
DAMAGE = "damage"
HEAL = "heal"
SKIP_TURN = "skip_turn"
END = "end"
ACTIONS = (DAMAGE, HEAL, SKIP_TURN, END)

# What a modifier changes.
DAMAGE_TAKEN = "damage_taken"
DAMAGE_DEALT = "damage_dealt"
STAT = "stat"
MODIFIER_KINDS = (DAMAGE_TAKEN, DAMAGE_DEALT, STAT)

GOOD = "good"
BAD = "bad"

FLAT = "flat"
RISING = "rising"
FALLING = "falling"
CURVES = (FLAT, RISING, FALLING)

# Why a token went.
EXPIRED = "expired"
REMOVED = "removed"
DIED = "died"
ENDED = "ended"


@dataclass(frozen=True)
class Curve:
    """The intensity over the time a status has: ``flat`` is always ``high``; ``falling`` starts at ``high`` and
    sinks to ``low`` as the last round arrives; ``rising`` starts at ``low`` and climbs to ``high``."""

    shape: str = FLAT
    high: float = 1.0
    low: float = 0.0


@dataclass(frozen=True)
class Tick:
    """Something a status does when ``when`` happens (every ``every``-th time)."""

    when: str
    action: str
    every: int = 1
    #: damage and heal: a flat amount plus a share (percent) of the maximum of ``resource`` (life, if empty).
    amount: int = 0
    percent: float = 0.0
    resource: str = ""
    attribute: str = "none"
    #: skip_turn: the chance, out of 100, that the turn is lost.
    chance: int = 100


@dataclass(frozen=True)
class Modifier:
    """Something that holds while the status lasts. ``damage_taken`` and ``damage_dealt``: damage of ``attribute``
    ("all" for any) is multiplied by ``factor`` (at full intensity; less intense, closer to 1).
    ``stat``: the current value of ``stat`` is raised or lowered by ``amount`` (times the intensity)."""

    kind: str
    attribute: str = "all"
    factor: float = 1.0
    stat: str = ""
    amount: int = 0


@dataclass(frozen=True)
class StatusSpec:
    key: str
    name: str = ""
    kind: str = BAD
    #: Rounds it lasts; None: until something removes it.
    duration: int | None = None
    curve: Curve = field(default_factory=Curve)
    ticks: tuple[Tick, ...] = ()
    modifiers: tuple[Modifier, ...] = ()
    #: The share (at full intensity) of the bearer's PXP the source is credited for each tick that moves no gauge.
    xp_share: float = 0.0


@dataclass
class StatusToken:
    spec: StatusSpec
    #: Who placed it: the fighter that earns from what it does.
    source: Address
    #: Rounds it lasts in all (None: no end).
    duration: int | None
    #: Rounds that have gone by since it was placed, and turns its bearer has taken.
    rounds: int = 0
    turns: int = 0

    @property
    def remaining(self) -> int | None:
        return None if self.duration is None else max(self.duration - self.rounds, 0)

    @property
    def intensity(self) -> float:
        return intensity_at(self.spec.curve, self.remaining, self.duration)


def intensity_at(curve: Curve, remaining: int | None, duration: int | None) -> float:
    """How strong a status is with ``remaining`` of its ``duration`` rounds left. Something with no end is flat.

    The time left runs from the full duration (just placed) to 1 (its last round); the curve is straight
    between its two ends: a falling status is at ``high`` when placed and at ``low`` on its last round."""
    if curve.shape == FLAT or duration is None or remaining is None or duration <= 1:
        return curve.high
    span = duration - 1
    position = min(max(duration - remaining, 0), span) / span  # 0 when placed, 1 on the last round
    if curve.shape == FALLING:
        return curve.high + (curve.low - curve.high) * position
    return curve.low + (curve.high - curve.low) * position


# --- the tokens on a fighter ------------------------------------------------------------------------------------

def find(fighter: "Combatant", key: str, source: Address) -> StatusToken | None:
    return next((token for token in fighter.tokens if token.spec.key == key and token.source == source), None)


def place(fighter: "Combatant", spec: StatusSpec, source: Address, duration: int | None) -> StatusToken:
    """Puts a token on ``fighter``. The same source placing the same status again refreshes its token; another
    source's token is its own, so each placer keeps the credit for what theirs does."""
    token = find(fighter, spec.key, source)
    if token is None:
        token = StatusToken(spec, source, duration)
        fighter.tokens.append(token)
        if spec.kind == GOOD and source not in fighter.buffed_by:
            fighter.buffed_by.append(source)
    else:
        token.duration, token.rounds = duration, 0
    return token


def take_off(fighter: "Combatant", key: str, source: Address) -> None:
    fighter.tokens[:] = [token for token in fighter.tokens if not (token.spec.key == key and token.source == source)]


def age(fighter: "Combatant") -> None:
    """A round has gone by. (Expiry is a separate event, ``StatusRemoved``, so it is in the log.)"""
    for token in fighter.tokens:
        token.rounds += 1


def count_turn(fighter: "Combatant") -> None:
    """The fighter has taken a turn."""
    for token in fighter.tokens:
        token.turns += 1


def expired(fighter: "Combatant") -> list[StatusToken]:
    return [token for token in fighter.tokens if token.duration is not None and token.rounds >= token.duration]


def due(token: StatusToken, tick: Tick, when: str) -> bool:
    """Whether $tick fires now, for the ``when`` that has just come: every ``every``-th one counts, from the first.
    Turns are counted by ``turns`` (the turn in progress is already counted), rounds by ``rounds`` (the round in
    progress is not yet). Only the four timed moments count; helped, harmed and saving throws fire every time."""
    if tick.when != when:
        return False
    if tick.every <= 1:
        return True
    if when in (TURN_START, TURN_END):
        number = token.turns
    elif when in (ROUND_START, ROUND_END):
        number = token.rounds + 1
    else:
        return True
    return number % tick.every == 0


# --- what a token does to numbers ---------------------------------------------------------------------------------

def scaled(spec_amount: float, intensity: float) -> int:
    return math.floor(spec_amount * intensity)


def tick_amount(tick: Tick, maximum: int, intensity: float) -> int:
    """What a damage or heal tick comes to: its flat amount plus its share of the maximum, times the intensity."""
    return scaled(tick.amount + maximum * tick.percent / 100.0, intensity)


def damage_factor(fighter: "Combatant", kind: str, attribute: str) -> float:
    """The product of every modifier of ``kind`` on ``fighter`` that covers ``attribute``, each eased towards 1 by
    its token's intensity (a factor of 0.5 at half intensity is 0.75)."""
    factor = 1.0
    for token in fighter.tokens:
        for modifier in token.spec.modifiers:
            if modifier.kind == kind and modifier.attribute in ("all", attribute):
                factor *= 1 + (modifier.factor - 1) * token.intensity
    return factor


def stat_bonus(fighter: "Combatant", stat: str) -> int:
    """What the fighter's tokens add to the current value of ``stat``."""
    return sum(
        math.floor(modifier.amount * token.intensity)
        for token in fighter.tokens for modifier in token.spec.modifiers
        if modifier.kind == STAT and modifier.stat == stat
    )


def adjusted_damage(dealer: "Combatant | None", target: "Combatant", damage: int, attribute: str) -> int:
    """Damage after the dealer's ``damage_dealt`` and the target's ``damage_taken`` modifiers. Healing (negative)
    is left alone. Damage that was at least 1 stays at least 1 unless a modifier cancels it (a factor of 0)."""
    if damage <= 0:
        return damage
    factor = damage_factor(target, DAMAGE_TAKEN, attribute)
    if dealer is not None:
        factor *= damage_factor(dealer, DAMAGE_DEALT, attribute)
    if factor <= 0:
        return 0
    return max(math.floor(damage * factor), 1)


def skips_turn(token: StatusToken, tick: Tick, rng: random.Random) -> bool:
    """Whether a skip_turn tick takes the turn. A chance of 100 is certain and draws nothing from the stream."""
    return tick.chance >= 100 or rng.randint(1, 100) <= tick.chance
