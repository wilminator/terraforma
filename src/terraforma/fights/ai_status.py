"""What statuses and stat changes are worth to the monster AI, in the same unit as damage (hit points).

DragonStar left these out: its AI values a status effect at nothing, with a note that this is temporary
"until effects are in place". They are in place now, so this is the engine's own estimate, not a DragonStar
number. It reads a status's definition (``StatusSpec``) and works out what it would do to its bearer over the
rounds it lasts, as a *benefit to the bearer*: positive is good for the one bearing it (a heal over time, a shield,
a raised stat), negative bad (poison, a lost turn, a lowered stat). The AI then wants a good status on its allies
and a bad one on its enemies, as it already does for healing and harm.

The estimate is deliberately plain, and a game that wants another overrides ``Rules.status_worth``:

* Every tick counts as often as its moment comes in a round (the start or end of a round or turn once, being helped
  or harmed half the time, a saving throw a quarter of it, each divided by its ``every``), over the rounds the
  status lasts (an endless one counts for ``EXPECTED_ROUNDS``), at the average of its intensity curve.
* A damage or heal tick is worth its amount; a skip-turn tick costs what the bearer's best attack would have done,
  times its chance.
* ``damage_taken`` of factor f is worth (1 - f) of a tenth of the bearer's life each round, ``damage_dealt`` of
  factor f is worth (f - 1) of the bearer's Strength each round, and a stat modifier is worth ``STAT_POINT`` hit points
  for each point.
"""

from . import status
from .combatant import Combatant
from .rules import Rules
from .specs import (
    CAUSE_BAD_STATUS,
    CAUSE_GOOD_STATUS,
    REMOVE_BAD_STATUS,
    REMOVE_GOOD_STATUS,
    SLAY,
    EffectSpec,
)

#: How many rounds a status with no end is counted for.
EXPECTED_ROUNDS = 3
#: The longest a status is counted for.
MAX_ROUNDS = 10
#: What one point of a stat is worth, in hit points.
STAT_POINT = 0.5
#: How often each moment comes, per round, for a tick.
FIRINGS = {
    status.ROUND_START: 1.0, status.ROUND_END: 1.0, status.TURN_START: 1.0, status.TURN_END: 1.0,
    status.HELPED: 0.5, status.HARMED: 0.5, status.SAVING_THROW: 0.25,
}  # fmt: skip


def _rounds(spec: status.StatusSpec, duration: int | None) -> int:
    return min(duration or spec.duration or EXPECTED_ROUNDS, MAX_ROUNDS)


def _mean_intensity(spec: status.StatusSpec, rounds: int) -> float:
    total = sum(status.intensity_at(spec.curve, rounds - number, rounds) for number in range(rounds))
    return total / max(rounds, 1)


def benefit(rules: Rules, spec: status.StatusSpec, bearer: Combatant, duration: int | None = None) -> float:
    """What carrying the status for ``duration`` rounds (its own, if None) is worth to ``bearer``: positive for good, negative for bad."""
    rounds = _rounds(spec, duration)
    intensity = _mean_intensity(spec, rounds)
    life = bearer.get_base(rules, rules.vital)
    worth = 0.0
    for tick in spec.ticks:
        times = FIRINGS.get(tick.when, 1.0) / max(tick.every, 1) * rounds
        if tick.action in (status.DAMAGE, status.HEAL):
            amount = status.tick_amount(tick, bearer.get_base(rules, tick.resource or rules.vital), intensity)
            worth += (amount if tick.action == status.HEAL else -amount) * times
        elif tick.action == status.SKIP_TURN:
            worth -= bearer.get_current(rules, "Strength") * tick.chance / 100.0 * times
    for modifier in spec.modifiers:
        if modifier.kind == status.DAMAGE_TAKEN:
            worth += (1 - modifier.factor) * intensity * life / 10.0 * rounds
        elif modifier.kind == status.DAMAGE_DEALT:
            worth += (modifier.factor - 1) * intensity * bearer.get_current(rules, "Strength") * rounds
        elif modifier.kind == status.STAT:
            worth += modifier.amount * intensity * STAT_POINT * rounds
    return worth


def effect_worth(rules: Rules, statuses: dict, target: Combatant, effect: EffectSpec) -> float | None:
    """What a status or stat effect is worth to the AI aiming it at ``target`` (always zero or more); None for any other effect."""
    kind = effect.effect
    if kind in (CAUSE_GOOD_STATUS, CAUSE_BAD_STATUS):
        spec = statuses.get(effect.status)
        if spec is None:
            return 0.0
        gain = benefit(rules, spec, target, effect.duration)
        return max(gain if kind == CAUSE_GOOD_STATUS else -gain, 0.0)
    if kind in (REMOVE_GOOD_STATUS, REMOVE_BAD_STATUS):
        wanted = status.GOOD if kind == REMOVE_GOOD_STATUS else status.BAD
        total = 0.0
        for token in target.tokens:
            if token.spec.kind == wanted and effect.status in ("", token.spec.key):
                gain = benefit(rules, token.spec, target, token.remaining)
                total += max(gain if kind == REMOVE_GOOD_STATUS else -gain, 0.0)
        return total
    if kind == SLAY:
        return effect.base / 100.0 * target.current[rules.vital]
    return None
