"""What a fighter's PXP counts about statuses and the effects that move life and mana (pure: no database).

Mike's rule (2026-10-16): being afflicted by a status does not change PXP; being able to cause or remove one does, and so does being
able to steal mana or pass life or mana to an ally."""

import pytest

from terraforma.fights import specs
from terraforma.fights.fight import build_fight
from terraforma.fights.potential import STATUS_DEFAULT_ROUNDS, STATUS_MAX_ROUNDS, STATUS_ROUND_WORTH, effect_rating
from terraforma.fights.specs import AbilitySpec, EffectSpec
from terraforma.fights.status import Curve, StatusSpec, StatusToken, Tick

from .test_fight_rules import RULES, fighter

STATUS_EFFECTS = [specs.CAUSE_BAD_STATUS, specs.CAUSE_GOOD_STATUS, specs.REMOVE_BAD_STATUS, specs.REMOVE_GOOD_STATUS]
MOVES = [specs.STEAL_MP, specs.TRANSFER_HP, specs.TRANSFER_MP]


def able(effect, kind="skill", mp=1):
    caster = fighter("Caster", MP=10)
    caster.abilities = [AbilitySpec("a", "A", kind, mp, effect)]
    return caster


@pytest.mark.parametrize("kind", STATUS_EFFECTS)
@pytest.mark.parametrize("ability_kind", ("skill", "spell"))
def test_being_able_to_cause_or_remove_a_status_raises_pxp(kind, ability_kind):
    plain = RULES.pxp(fighter("Plain"))
    assert RULES.pxp(able(EffectSpec(kind, specs.INDIVIDUAL, status="poisoned"), ability_kind)) > plain


@pytest.mark.parametrize("kind", MOVES)
def test_stealing_mana_and_passing_life_or_mana_raise_pxp(kind):
    assert RULES.pxp(able(EffectSpec(kind, specs.INDIVIDUAL, base=8))) > RULES.pxp(fighter("Plain"))


def test_a_move_is_rated_like_the_steal_of_life_and_a_heal():
    for kind in MOVES:
        assert effect_rating(EffectSpec(kind, specs.INDIVIDUAL, base=8, added=4)) == effect_rating(EffectSpec(specs.STEAL_HP, specs.INDIVIDUAL, base=8, added=4)) == 10


def test_a_status_effect_is_rated_by_the_rounds_it_lasts_and_an_ability_that_cannot_be_paid_for_counts_for_nothing():
    one = effect_rating(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="stunned", duration=1))
    three = effect_rating(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="stunned", duration=3))
    assert (one, three) == (STATUS_ROUND_WORTH, 3 * STATUS_ROUND_WORTH)
    assert effect_rating(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="stunned")) == STATUS_DEFAULT_ROUNDS * STATUS_ROUND_WORTH
    assert effect_rating(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="stunned", duration=500)) == STATUS_MAX_ROUNDS * STATUS_ROUND_WORTH
    assert RULES.pxp(able(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="stunned", duration=3), mp=11)) == RULES.pxp(fighter("Plain"))


def test_a_wider_reach_is_worth_more_as_it_is_for_damage():
    single = RULES.pxp(able(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="asleep", duration=3)))
    group = RULES.pxp(able(EffectSpec(specs.CAUSE_BAD_STATUS, specs.GROUP, status="asleep", duration=3)))
    assert group > single


def test_a_longer_status_is_worth_more():
    short = RULES.pxp(able(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="asleep", duration=1)))
    long = RULES.pxp(able(EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="asleep", duration=5)))
    assert long > short


def test_being_afflicted_or_blessed_does_not_change_pxp():
    for kind, curve in (("bad", Curve()), ("good", Curve())):
        bearer = fighter("Bearer")
        before = RULES.pxp(bearer)
        poison = StatusSpec("s", "S", kind, 3, curve=curve, ticks=(Tick("round_end", "damage", amount=5),))
        fight = build_fight({0: {0: [bearer]}, 1: {0: [fighter("Foe")]}}, {"s": poison})
        bearer.tokens.append(StatusToken(poison, (1, 0, 0), 3, 0))
        assert RULES.pxp(bearer) == before == RULES.pxp(fight.get((0, 0, 0)))
