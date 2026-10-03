"""The monster AI and statuses: what a status or a stat change is worth, and how Protector, Hinderer, Smart and Omnipotent play."""

import pytest

from terraforma.fights import status
from terraforma.fights.ai import Action, Aim, Choice, Goal, choose_command
from terraforma.fights.ai_status import benefit, effect_worth
from terraforma.fights.combatant import Command
from terraforma.fights.rules import Rules
from terraforma.fights.specs import AbilitySpec, EffectSpec
from terraforma.fights.status import BAD, GOOD, Curve, Modifier, StatusSpec, Tick

from .test_ai import RULES, fight_of, fighter, play, rng, spell

POISON = StatusSpec("poison", kind=BAD, duration=3, ticks=(Tick(status.ROUND_END, status.DAMAGE, amount=10),))
REGEN = StatusSpec("regen", kind=GOOD, duration=3, ticks=(Tick(status.ROUND_END, status.HEAL, amount=5),))
STUN = StatusSpec("stun", kind=BAD, duration=2, ticks=(Tick(status.TURN_START, status.SKIP_TURN, chance=50),))
SHIELD = StatusSpec("shield", kind=GOOD, duration=3, modifiers=(Modifier(status.DAMAGE_TAKEN, factor=0.5),))
RAGE = StatusSpec("rage", kind=GOOD, duration=2, modifiers=(Modifier(status.STAT, stat="Strength", amount=10),))
SPECS = {each.key: each for each in (POISON, REGEN, STUN, SHIELD, RAGE)}


def effect(kind, **fields):
    return EffectSpec(kind, **fields)


def venom(cost=0):
    """A skill that poisons (the status the fight knows as "poison")."""
    return AbilitySpec("venom", "Venom", "skill", cost, EffectSpec("cause_bad_status", status="poison"))


def strength(me, value):
    me.base["Strength"] = me.current["Strength"] = value
    return me


def with_statuses(fight):
    fight.statuses = dict(SPECS)
    return fight


# --- what things are worth ----------------------------------------------------------------------------------------------

def test_what_a_status_is_worth_to_its_bearer():
    bearer = fighter()  # life 100, Strength 100
    assert benefit(RULES, POISON, bearer) == -30  # 10 each round end, 3 rounds
    assert benefit(RULES, REGEN, bearer) == 15
    assert benefit(RULES, STUN, bearer) == -100.0  # a lost turn costs its best attack: 100 x 50% x 2 rounds
    assert benefit(RULES, SHIELD, bearer) == pytest.approx(15)  # half the damage of a tenth of its life, 3 rounds
    assert benefit(RULES, RAGE, bearer) == pytest.approx(10)  # +10 for 2 rounds at half a hit point each
    assert benefit(RULES, POISON, bearer, duration=1) == -10  # a shorter stay is worth less


def test_a_falling_status_is_worth_less_than_a_flat_one():
    fading = StatusSpec("fade", kind=BAD, duration=3, curve=Curve("falling", 1.0, 0.0), ticks=POISON.ticks)
    assert -30 < benefit(RULES, fading, fighter()) < 0


def test_what_an_effect_is_worth_to_the_one_aiming_it():
    target = fighter()
    poisoned = fighter()
    status.place(poisoned, POISON, (0, 0, 0), 3)
    assert effect_worth(RULES, SPECS, target, effect("cause_bad_status", status="poison")) == 30
    assert effect_worth(RULES, SPECS, target, effect("cause_good_status", status="regen")) == 15
    assert effect_worth(RULES, SPECS, target, effect("cause_good_status", status="poison")) == 0, "never less than nothing"
    assert effect_worth(RULES, SPECS, target, effect("cause_bad_status", status="nothing-like-it")) == 0
    assert effect_worth(RULES, SPECS, poisoned, effect("remove_bad_status")) == 30
    assert effect_worth(RULES, SPECS, poisoned, effect("remove_bad_status", status="stun")) == 0
    assert effect_worth(RULES, SPECS, target, effect("remove_bad_status")) == 0
    assert effect_worth(RULES, SPECS, poisoned, effect("remove_good_status")) == 0
    assert effect_worth(RULES, SPECS, target, effect("slay", base=50)) == 50
    assert effect_worth(RULES, SPECS, target, effect("hurt", base=5)) is None


def test_a_game_can_value_statuses_its_own_way():
    class Stingy(Rules):
        def status_worth(self, statuses, target, effect):
            return 7

    assert Stingy().status_worth(SPECS, fighter(), effect("cause_bad_status", status="poison")) == 7
    assert RULES.status_worth(SPECS, fighter(), effect("cause_bad_status", status="poison")) == 30


# --- how the four actions play -------------------------------------------------------------------------------------------

def test_a_hinderer_wears_the_enemy_down_before_it_hits():
    me = strength(fighter(ai_action=Action.HINDERER, ai_goal=Goal.DESTRUCTOR, abilities=[venom()]), 10)
    fight = with_statuses(fight_of([[me]], [[fighter(), fighter(block=50)]]))
    for n in range(10):
        choice = play(fight, (0, 0, 0), n)
        assert choice.command == Command.SKILL and choice.target[0] == 1


def test_a_hinderer_with_nothing_to_hinder_with_fights():
    me = fighter(ai_action=Action.HINDERER, ai_goal=Goal.DESTRUCTOR)
    fight = with_statuses(fight_of([[me]], [[fighter()]]))
    assert play(fight, (0, 0, 0)) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 0))


def test_a_protector_cleanses_an_ally_under_a_bad_status():
    cleanse = spell("remove_bad_status", cost=0, kind="skill")
    me = fighter(ai_action=Action.PROTECTOR, ai_goal=Goal.DESTRUCTOR, abilities=[cleanse])
    sick = fighter()
    status.place(sick, POISON, (1, 0, 0), 3)
    fight = with_statuses(fight_of([[me, sick]], [[fighter()]]))
    assert play(fight, (0, 0, 0)) == Choice(Command.SKILL, 0, (0, 0, 1))


def test_a_protector_with_no_one_to_help_plays_normal():
    cleanse = spell("remove_bad_status", cost=0, kind="skill")
    me = fighter(ai_action=Action.PROTECTOR, ai_goal=Goal.DESTRUCTOR, abilities=[cleanse])
    fight = with_statuses(fight_of([[me, fighter()]], [[fighter()]]))
    for n in range(10):
        assert play(fight, (0, 0, 0), n).command in (Command.ATTACK_LEFT, Command.SKILL)


def test_a_smart_fighter_takes_the_best_command_it_has_not_a_random_one():
    flame = spell("hurt", base=20, added=10, cost=0, kind="skill")  # worth 25
    me = strength(fighter(ai_action=Action.SMART, ai_goal=Goal.DESTRUCTOR, abilities=[flame, venom()]), 10)
    fight = with_statuses(fight_of([[me]], [[fighter()]]))
    for n in range(10):
        assert play(fight, (0, 0, 0), n) == Choice(Command.SKILL, 1, (1, 0, 0))  # the poison, worth 30
    strong = fighter(ai_action=Action.SMART, ai_goal=Goal.DESTRUCTOR, abilities=[flame, venom()])
    fight = with_statuses(fight_of([[strong]], [[fighter()]]))
    assert play(fight, (0, 0, 0)) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 0))  # its sword is worth 100


def test_an_omnipotent_fighter_reads_every_stat_exactly_and_keeps_its_experience():
    sword_vs_wall = fighter(ai_action=Action.OMNIPOTENT, ai_goal=Goal.DESTRUCTOR, ai_experience=50, ai_target=Aim.OMNIPOTENT)
    fight = fight_of([[sword_vs_wall]], [[fighter(block=60), fighter(block=10)]])
    for n in range(10):
        assert play(fight, (0, 0, 0), n) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 1))  # the softer of the two, however it is skewed
    assert sword_vs_wall.ai_experience == 50


@pytest.mark.parametrize("action", [Action.PROTECTOR, Action.HINDERER, Action.SMART, Action.OMNIPOTENT])
def test_the_new_plays_choose_from_the_fights_stream_alone(action):
    cleanse = spell("remove_bad_status", cost=0, kind="skill")
    me = fighter(ai_action=action, ai_goal=Goal.SCHEMER, ai_experience=30, abilities=[venom(), cleanse])
    fight = with_statuses(fight_of([[me, fighter(hp=20)]], [[fighter(), fighter(block=30)]]))
    assert choose_command(RULES, fight, (0, 0, 0), rng("same")) == choose_command(RULES, fight, (0, 0, 0), rng("same"))
