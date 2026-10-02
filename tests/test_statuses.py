"""Statuses: tokens that tick, modifiers, direct stat adjustment, who placed what, and the seed format.

Pure code, so most of these need no database. A ``Scripted`` stream (from test_fight_rules) hands out exactly the rolls
a test chooses. A fighter told to do nothing defends, so a round with no commands plays only what the statuses do.
"""

import copy
import json

import pytest

from sqlalchemy import select

from terraforma.content import models
from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import specs, status, store
from terraforma.fights.build import known_statuses
from terraforma.fights.combatant import Command
from terraforma.fights.content import status_spec
from terraforma.fights.events import Event, EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.replay import apply_events
from terraforma.fights.resolve import do_combat, fight_stream
from terraforma.fights.rules import Rules
from terraforma.fights.specs import EffectSpec, ItemSpec
from terraforma.fights.state import dehydrate, hydrate
from terraforma.fights.status import BAD, GOOD, Curve, Modifier, StatusSpec, StatusToken, Tick
from terraforma.world.rng import WorldRng
from terraforma.world.start import ensure_start

from .test_fight_rules import RULES, Scripted, fighter, listing, sword, types

A, B = (0, 0, 0), (1, 0, 0)


def poison(**changes) -> StatusSpec:
    changes = {"duration": 3, "ticks": (Tick(status.ROUND_END, status.DAMAGE, amount=4),), **changes}
    return StatusSpec("poison", "Poison", BAD, **changes)


def duel_with(*specs_, bearer_hp=20):
    """A fighting B, both idle; the statuses known to the fight."""
    a, b = fighter("A"), fighter("B", HP=bearer_hp)
    return build_fight({0: {0: [a]}, 1: {0: [b]}}, {each.key: each for each in specs_}), a, b


def bear(fighter_, spec, source=A, duration=None, rounds=0):
    token = StatusToken(spec, source, duration if duration is not None else spec.duration, rounds)
    fighter_.tokens.append(token)
    return token


def play(fight, rolls=(), rules=RULES):
    return do_combat(fight, rules, Scripted(*rolls))


# --- intensity ------------------------------------------------------------------------------------------------

def test_intensity_follows_the_time_left_flat_falling_or_rising():
    flat, falling, rising = Curve(status.FLAT), Curve(status.FALLING), Curve(status.RISING)
    assert [status.intensity_at(flat, left, 4) for left in (4, 3, 2, 1)] == [1.0] * 4
    assert [status.intensity_at(falling, left, 4) for left in (4, 3, 2, 1)] == pytest.approx([1.0, 2 / 3, 1 / 3, 0.0])
    assert [status.intensity_at(rising, left, 4) for left in (4, 3, 2, 1)] == pytest.approx([0.0, 1 / 3, 2 / 3, 1.0])


def test_the_curve_runs_between_its_own_low_and_high_and_something_without_an_end_is_flat():
    assert status.intensity_at(Curve(status.FALLING, high=2.0, low=0.5), 3, 3) == 2.0
    assert status.intensity_at(Curve(status.FALLING, high=2.0, low=0.5), 1, 3) == 0.5
    assert status.intensity_at(Curve(status.FALLING), None, None) == 1.0, "no duration: no time to fall over"
    assert status.intensity_at(Curve(status.FALLING), 1, 1) == 1.0, "a status of one round is at full strength"


def test_a_token_counts_down_from_its_duration_as_rounds_go_by():
    token = StatusToken(poison(duration=3, curve=Curve(status.FALLING)), A, 3)
    assert (token.remaining, token.intensity) == (3, 1.0)
    token.rounds = 2
    assert (token.remaining, token.intensity) == (1, 0.0)
    assert StatusToken(poison(duration=None), A, None).remaining is None


# --- who placed it --------------------------------------------------------------------------------------------------

def test_the_same_source_placing_a_status_again_refreshes_it_and_another_source_adds_its_own():
    fight, _a, b = duel_with(poison())
    first = status.place(b, fight.statuses["poison"], A, 3)
    first.rounds = 2
    assert status.place(b, fight.statuses["poison"], A, 3) is first and first.rounds == 0 and len(b.tokens) == 1
    status.place(b, fight.statuses["poison"], (0, 0, 1), 3)
    assert [token.source for token in b.tokens] == [A, (0, 0, 1)]
    status.take_off(b, "poison", A)
    assert [token.source for token in b.tokens] == [(0, 0, 1)]


class Watching(Rules):
    """The default rules, writing down what the experience hooks are told."""

    def __init__(self):
        self.moved, self.acted = [], []

    def gauge_moved(self, fight, actor, target, resource, before, after, maximum):
        self.moved.append((actor, target, resource, before, after, maximum))
        return []

    def status_acted(self, fight, source, target, status_, intensity, ratio):
        self.acted.append((source, target, status_.key, intensity, ratio))
        return []


def test_a_tick_that_hurts_is_the_sources_doing_so_the_experience_rules_credit_the_source():
    rules = Watching()
    fight, _a, b = duel_with(poison())
    bear(b, fight.statuses["poison"], source=A)
    play(fight, rules=rules)
    assert rules.moved == [(A, B, "HP", 20, 16, 20)], "the placer is the actor, so it is the one that earns"
    assert rules.acted == [], "a tick that moved a gauge has been reported already"


def test_a_tick_that_moves_no_gauge_reports_a_share_of_the_bearers_worth_scaled_by_intensity():
    rules = Watching()
    hex_ = StatusSpec("hex", "Hex", BAD, duration=3, curve=Curve(status.FALLING), xp_share=0.5,
                      ticks=(Tick(status.TURN_START, status.SKIP_TURN),))
    fight, _a, b = duel_with(hex_)
    bear(b, hex_, source=A, rounds=0)
    b.command, b.target = Command.ATTACK_LEFT, A
    play(fight, [100], rules)
    assert rules.acted == [(A, B, "hex", 1.0, 0.5)]
    # A round later it has weakened: half intensity, half the share.
    rules.acted.clear()
    b.tokens[0].rounds = 1
    play(fight, [100], rules)
    assert rules.acted == [(A, B, "hex", 0.5, 0.25)]


def test_the_rules_are_not_asked_when_a_status_has_no_share_to_give():
    rules = Watching()
    quiet = StatusSpec("quiet", "Quiet", BAD, duration=2, ticks=(Tick(status.TURN_START, status.SKIP_TURN),))
    fight, _a, b = duel_with(quiet)
    bear(b, quiet)
    b.command, b.target = Command.ATTACK_LEFT, A
    play(fight, [100], rules)
    assert rules.acted == [(A, B, "quiet", 1.0, 0.0)], "a skipped turn is always reported, with a ratio of nothing"


# --- ticks ---------------------------------------------------------------------------------------------------------------

def test_a_status_ticks_at_the_end_of_each_round_ages_and_ends_after_its_duration():
    fight, _a, b = duel_with(poison())
    bear(b, fight.statuses["poison"])
    tick = ("StatusTick", 1, 0, 0, "poison", 0, 0, 0, 1.0, "round_end")
    assert listing(play(fight)) == [tick, ("Damage", 1, 0, 0, 4, False), ("RoundEnd",)]
    assert listing(play(fight)) == [tick, ("Damage", 1, 0, 0, 4, False), ("RoundEnd",)]
    assert listing(play(fight)) == [tick, ("Damage", 1, 0, 0, 4, False), ("RoundEnd",), ("StatusRemoved", 1, 0, 0, "poison", 0, 0, 0, "expired")]
    assert b.current["HP"] == 8 and b.tokens == []
    assert play(fight) == [], "nothing left: nothing happens, and no RoundEnd is logged for a fight with no tokens"


def test_a_falling_status_hurts_less_each_round_until_it_is_gone():
    fight, _a, b = duel_with(poison(curve=Curve(status.FALLING), ticks=(Tick(status.ROUND_END, status.DAMAGE, amount=9),)))
    bear(b, fight.statuses["poison"])
    hurt = []
    for _ in range(3):
        hurt.append([each.data[3] for each in play(fight) if each.type is EventType.DAMAGE])
    assert hurt == [[9], [4], []], "9, then half of it, then nothing at the end of its last round"
    assert 20 - b.current["HP"] == 13


def test_a_percent_tick_takes_a_share_of_the_maximum():
    fight, _a, b = duel_with(StatusSpec("burn", "Burn", BAD, 2, ticks=(Tick(status.ROUND_END, status.DAMAGE, percent=25.0),)))
    bear(b, fight.statuses["burn"])
    play(fight)
    assert b.current["HP"] == 15


def test_a_tick_can_heal_and_can_work_on_another_resource():
    regen = StatusSpec("regen", "Regen", GOOD, 4, ticks=(Tick(status.ROUND_END, status.HEAL, amount=3),
                                                         Tick(status.ROUND_END, status.HEAL, amount=2, resource="MP")))
    fight, _a, b = duel_with(regen, bearer_hp=20)
    b.current["HP"], b.current["MP"] = 10, 5
    bear(b, regen)
    play(fight)
    assert (b.current["HP"], b.current["MP"]) == (13, 7)
    drain = StatusSpec("drain", "Drain", BAD, 2, ticks=(Tick(status.ROUND_END, status.DAMAGE, amount=50, resource="MP"),))
    fight, _a, b = duel_with(drain)
    bear(b, drain)
    events = play(fight)
    assert b.current["MP"] == 0 and ("AlterStat", 1, 0, 0, "MP", -10) in listing(events), "it can't take more than there is"


def test_every_nth_only_fires_on_each_nth_round():
    slow = StatusSpec("slow_burn", "Slow burn", BAD, 6, ticks=(Tick(status.ROUND_END, status.DAMAGE, amount=1, every=2),))
    fight, _a, b = duel_with(slow)
    bear(b, slow)
    hurt = [sum(1 for each in play(fight) if each.type is EventType.DAMAGE) for _ in range(6)]
    assert hurt == [0, 1, 0, 1, 0, 1]


def test_every_nth_turn_counts_the_bearers_own_turns():
    jinx = StatusSpec("jinx", "Jinx", BAD, None, ticks=(Tick(status.TURN_END, status.DAMAGE, amount=2, every=2),))
    fight, _a, b = duel_with(jinx)
    bear(b, jinx)
    b.command, b.target = Command.ATTACK_LEFT, A
    # Round 1: A and B both idle except B, who attacks A: needs a hit roll of 50 (A dodges at 1).
    first = play(fight, [100, 50])
    second = play(fight, [100, 50])
    assert [sum(1 for each in events if each.type is EventType.STATUS_TICK) for events in (first, second)] == [0, 1]
    assert b.current["HP"] == 18, "only the second turn ended with a tick"


def test_skipping_a_turn_loses_the_action_and_a_chance_below_100_rolls_for_it():
    sleep = StatusSpec("sleep", "Sleep", BAD, 3, ticks=(Tick(status.TURN_START, status.SKIP_TURN, chance=50),))
    fight, _a, b = duel_with(sleep)
    bear(b, sleep)
    b.command, b.target = Command.ATTACK_LEFT, A
    skipped = play(fight, [100, 50])
    assert types(skipped)[:3] == [EventType.TURN, EventType.STATUS_TICK, EventType.TURN_SKIPPED]
    assert EventType.ATTACK not in types(skipped)
    acted = play(fight, [100, 51, 50])
    assert EventType.TURN_SKIPPED not in types(acted) and EventType.ATTACK in types(acted), "a roll of 51 keeps the turn"


def test_being_harmed_can_end_a_status_so_the_sleeper_wakes_and_acts_in_the_same_round():
    sleep = StatusSpec("sleep", "Sleep", BAD, 5, ticks=(Tick(status.TURN_START, status.SKIP_TURN), Tick(status.HARMED, status.END)))
    fast, slow = fighter("Fast", Speed=30), fighter("Slow", Speed=5)
    fight = build_fight({0: {0: [fast]}, 1: {0: [slow]}}, {"sleep": sleep})
    bear(slow, sleep)
    for each, target in ((fast, B), (slow, A)):
        each.command, each.target = Command.ATTACK_LEFT, target
    events = play(fight, [100, 100, 50, 50])
    kinds = types(events)
    assert kinds.index(EventType.STATUS_REMOVED) < len(kinds) - 1 - kinds[::-1].index(EventType.TURN), "gone before the sleeper's turn"
    assert ("StatusRemoved", 1, 0, 0, "sleep", 0, 0, 0, "ended") in listing(events)
    assert EventType.TURN_SKIPPED not in kinds and kinds.count(EventType.ATTACK) == 2
    assert slow.tokens == []


def test_a_tick_set_off_by_harm_is_not_itself_a_harm_that_sets_off_more():
    thorns = StatusSpec("thorns", "Thorns", BAD, 2, ticks=(Tick(status.HARMED, status.DAMAGE, amount=1), Tick(status.ROUND_END, status.DAMAGE, amount=2)))
    fight, _a, b = duel_with(thorns)
    bear(b, thorns)
    assert types(play(fight)) == [EventType.STATUS_TICK, EventType.DAMAGE, EventType.ROUND_END]
    assert b.current["HP"] == 18


def test_a_saving_throw_sets_off_the_ticks_of_the_one_who_made_it():
    ward = StatusSpec("ward", "Ward", GOOD, 3, ticks=(Tick(status.SAVING_THROW, status.HEAL, amount=2),))
    caster, target = fighter("Caster", Power=10), fighter("Target", Resistance=100)
    caster.abilities = [specs.AbilitySpec("zap", "Zap", "spell", 1, EffectSpec(specs.HURT, specs.INDIVIDUAL, base=3))]
    caster.command, caster.using, caster.target = Command.SPELL, 0, B
    fight = build_fight({0: {0: [caster]}, 1: {0: [target]}}, {"ward": ward})
    target.current["HP"] = 10
    bear(target, ward)
    # Speed and Focus (casting slows the caster, and Focus is rolled too); the saving throw, a 1 (all through); the damage roll.
    events = listing(play(fight, [100, 100, 1, 0]))
    assert ("StatusTick", 1, 0, 0, "ward", 0, 0, 0, 1.0, "saving_throw") in events
    assert events.index(("StatusTick", 1, 0, 0, "ward", 0, 0, 0, 1.0, "saving_throw")) < [each[0] for each in events].index("Damage")
    assert target.current["HP"] == 10 + 2 - 3, "healed 2 by the ward when it rolled, then hurt for 3"


def test_a_status_on_the_dead_does_nothing_and_goes_at_the_end_of_the_round():
    fight, _a, b = duel_with(poison(), bearer_hp=2)
    bear(b, fight.statuses["poison"])
    kinds = listing(play(fight))
    assert kinds == [("StatusTick", 1, 0, 0, "poison", 0, 0, 0, 1.0, "round_end"), ("Damage", 1, 0, 0, 4, False), ("Died", 1, 0, 0, 4, 2),
                     ("StatusRemoved", 1, 0, 0, "poison", 0, 0, 0, "died")]
    assert b.tokens == []


# --- modifiers ----------------------------------------------------------------------------------------------------------------

def test_damage_taken_of_a_kind_is_changed_and_a_weaker_status_changes_it_less():
    resist = StatusSpec("resist", "Resist", GOOD, 3, Curve(status.FALLING), modifiers=(Modifier(status.DAMAGE_TAKEN, "fire", 0.5),))
    victim, dealer = fighter("Victim"), fighter("Dealer")
    token = StatusToken(resist, A, 3)
    victim.tokens.append(token)
    assert status.adjusted_damage(dealer, victim, 10, "fire") == 5
    assert status.adjusted_damage(dealer, victim, 10, "ice") == 10, "only the kind it names"
    token.rounds = 1  # half intensity: a factor of 0.5 eases to 0.75
    assert status.adjusted_damage(dealer, victim, 10, "fire") == 7
    assert status.adjusted_damage(dealer, victim, -10, "fire") == -10, "healing is left alone"


def test_damage_dealt_and_the_all_kind_and_immunity():
    rage = StatusSpec("rage", "Rage", GOOD, 3, modifiers=(Modifier(status.DAMAGE_DEALT, "all", 2.0),))
    ward = StatusSpec("ward", "Ward", GOOD, 3, modifiers=(Modifier(status.DAMAGE_TAKEN, "holy", 0.0),))
    victim, dealer = fighter("Victim"), fighter("Dealer")
    bear(dealer, rage)
    assert status.adjusted_damage(dealer, victim, 6, "fire") == 12
    bear(victim, ward)
    assert status.adjusted_damage(dealer, victim, 6, "holy") == 0, "a factor of 0 cancels it"
    assert status.adjusted_damage(dealer, victim, 1, "fire") == 2


def test_a_tick_that_damages_is_changed_by_the_bearers_modifiers_too():
    fight, _a, b = duel_with(poison(), StatusSpec("resist", "Resist", GOOD, 3, modifiers=(Modifier(status.DAMAGE_TAKEN, "poison", 0.5),)))
    fight.statuses["poison"] = poison(ticks=(Tick(status.ROUND_END, status.DAMAGE, amount=10, attribute="poison"),))
    bear(b, fight.statuses["poison"])
    bear(b, fight.statuses["resist"])
    play(fight)
    assert b.current["HP"] == 15


def test_a_stat_modifier_changes_the_current_value_while_it_lasts_scaled_by_intensity():
    haste = StatusSpec("haste", "Haste", GOOD, 3, Curve(status.FALLING), modifiers=(Modifier(status.STAT, stat="Speed", amount=10),))
    runner = fighter("Runner", Speed=10)
    token = bear(runner, haste)
    assert runner.get_current(RULES, "Speed") == 20 and runner.get_base(RULES, "Speed") == 10
    token.rounds = 1
    assert runner.get_current(RULES, "Speed") == 15
    runner.tokens.clear()
    assert runner.get_current(RULES, "Speed") == 10


# --- effects that place and remove statuses ----------------------------------------------------------------------------------

def item(key, effect) -> ItemSpec:
    return ItemSpec(key=key, name=key, use_effect=effect)


def use(fighter_, which, target):
    fighter_.command, fighter_.using, fighter_.target = Command.ITEM, which, target


def test_an_effect_places_a_status_with_the_user_as_its_source_and_its_own_duration():
    marked = StatusSpec("marked", "Marked", BAD, 5)
    a, b = fighter("A"), fighter("B")
    a.inventory = [[item("brand", EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="marked", duration=2)), 1]]
    use(a, 0, B)
    fight = build_fight({0: {0: [a]}, 1: {0: [b]}}, {"marked": marked})
    events = play(fight, [100])
    assert ("StatusApplied", 1, 0, 0, "marked", 0, 0, 0, 2) in listing(events)
    assert [(token.source, token.duration, token.rounds) for token in b.tokens] == [(A, 2, 1)]


def test_the_status_s_own_duration_is_used_when_the_effect_has_none():
    marked = StatusSpec("marked", "Marked", BAD, 5)
    a, b = fighter("A"), fighter("B")
    a.inventory = [[item("brand", EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="marked")), 1]]
    use(a, 0, B)
    play(build_fight({0: {0: [a]}, 1: {0: [b]}}, {"marked": marked}), [100])
    assert b.tokens[0].duration == 5


def test_two_users_each_keep_their_own_token_and_one_refreshing_leaves_the_other_running_down():
    marked = StatusSpec("marked", "Marked", BAD, 2)
    brand = item("brand", EffectSpec(specs.CAUSE_BAD_STATUS, specs.INDIVIDUAL, status="marked"))
    one, two, b = fighter("One", Speed=20), fighter("Two", Speed=10), fighter("B")
    one.inventory, two.inventory = [[brand, 1]], [[brand, 1]]
    use(one, 0, B), use(two, 0, B)
    fight = build_fight({0: {0: [one, two]}, 1: {0: [b]}}, {"marked": marked})
    play(fight, [100, 100])
    assert sorted(token.source for token in b.tokens) == [(0, 0, 0), (0, 0, 1)] and all(token.rounds == 1 for token in b.tokens)
    two.command = Command.DEFEND
    events = play(fight, [100])
    assert [(token.source, token.rounds) for token in b.tokens] == [((0, 0, 0), 1)], "One's was refreshed; Two's ran out"
    assert ("StatusRemoved", 1, 0, 0, "marked", 0, 0, 1, "expired") in listing(events)


def test_removing_statuses_takes_off_only_the_kind_named_and_a_key_narrows_it():
    bad1, bad2, good = StatusSpec("poison", "Poison", BAD, 5), StatusSpec("weak", "Weak", BAD, 5), StatusSpec("haste", "Haste", GOOD, 5)
    a, b = fighter("A"), fighter("B")
    a.inventory = [[item("cleanse", EffectSpec(specs.REMOVE_BAD_STATUS)), 1], [item("purge", EffectSpec(specs.REMOVE_GOOD_STATUS, status="haste")), 1],
                   [item("antidote", EffectSpec(specs.REMOVE_BAD_STATUS, status="poison")), 1]]
    fight = build_fight({0: {0: [a]}, 1: {0: [b]}}, {each.key: each for each in (bad1, bad2, good)})
    for spec in (bad1, bad2, good):
        bear(b, spec, duration=5)
    use(a, 2, B)
    play(fight, [100])
    assert sorted(token.spec.key for token in b.tokens) == ["haste", "weak"], "just the poison"
    use(a, 0, B)
    play(fight, [100])
    assert [token.spec.key for token in b.tokens] == ["haste"], "every bad one, never the good"
    use(a, 1, B)
    play(fight, [100])
    assert b.tokens == []


def test_placing_a_status_the_fight_does_not_know_is_an_error_not_a_silent_nothing():
    a, b = fighter("A"), fighter("B")
    a.inventory = [[item("brand", EffectSpec(specs.CAUSE_BAD_STATUS, status="nope")), 1]]
    use(a, 0, B)
    with pytest.raises(ValueError, match="nope"):
        play(build_fight({0: {0: [a]}, 1: {0: [b]}}), [100])


def test_slay_kills_on_a_roll_within_its_chance_and_otherwise_has_no_effect():
    def slay(roll):
        a, b = fighter("A"), fighter("B")
        a.inventory = [[item("noose", EffectSpec(specs.SLAY, base=30)), 1]]
        use(a, 0, B)
        return listing(play(build_fight({0: {0: [a]}, 1: {0: [b]}}), [100, roll]))

    assert slay(30)[-2:] == [("Damage", 1, 0, 0, 20, False), ("Died", 1, 0, 0, 20, 0)]
    assert slay(31)[-1] == ("NoEffect", 1, 0, 0)


# --- direct stat adjustment -------------------------------------------------------------------------------------------------------------

def stat_fight(effect, **extra):
    a, b = fighter("A", **extra), fighter("B")
    a.inventory = [[item("tonic", effect), 1]]
    use(a, 0, B)
    return build_fight({0: {0: [a]}, 1: {0: [b]}}), a, b


def test_a_decrease_pushes_the_current_value_down_and_it_drifts_back_to_the_base_over_the_rounds():
    fight, _a, b = stat_fight(EffectSpec(specs.DECREASE_STATS, base=4, stats=("Strength",)))
    events = play(fight, [100, 0])
    assert listing(events)[-2:] == [("AlterStat", 1, 0, 0, "Strength", -4), ("AlterStat", 1, 0, 0, "Strength", 1)]
    assert (b.current["Strength"], b.base["Strength"]) == (7, 10)
    fight.get(A).command = Command.DEFEND
    seen = []
    for _ in range(4):
        play(fight)
        seen.append(b.current["Strength"])
    assert seen == [8, 9, 10, 10], "a step of a quarter of the gap, at least 1, then rest"


def test_a_big_gap_drifts_faster_and_a_drift_never_overshoots():
    assert RULES.stat_drift("Strength", 0, 40) == 10
    assert RULES.stat_drift("Strength", 39, 40) == 1
    assert RULES.stat_drift("Strength", 50, 40) == -2
    assert RULES.stat_drift("Strength", 40, 40) == 0


def test_stats_are_pushed_only_within_their_range():
    fight, a, _b = stat_fight(EffectSpec(specs.INCREASE_STATS, specs.INDIVIDUAL, base=50, stats=("Strength",)))
    use(a, 0, A)
    events = play(fight, [100, 0])
    assert ("AlterStat", 0, 0, 0, "Strength", 10) in listing(events), "from 10 up to the ceiling of 20, no further"
    assert a.current["Strength"] == 18, "and 20 drifts a quarter of the way back at the end of the round"
    fight, _a, b = stat_fight(EffectSpec(specs.DECREASE_STATS, base=50, stats=("Strength",)))
    events = play(fight, [100, 0])
    assert ("AlterStat", 1, 0, 0, "Strength", -10) in listing(events), "down to zero and no further"
    assert b.current["Strength"] == 2


def test_a_stat_effect_never_touches_a_resource():
    fight, _a, b = stat_fight(EffectSpec(specs.DECREASE_STATS, base=4, stats=("HP", "Strength")))
    play(fight, [100, 0])
    assert b.current["HP"] == 20 and b.current["Strength"] == 7


def test_steal_takes_from_the_target_and_gives_to_the_user():
    fight, a, b = stat_fight(EffectSpec(specs.STEAL_STATS, base=3, stats=("Strength",)))
    play(fight, [100, 0])
    # 3 stolen, then each drifts a step back towards its own base at the end of the round.
    assert (b.current["Strength"], a.current["Strength"]) == (8, 12)


def test_a_stat_pushed_in_one_round_is_replayed_like_the_rest():
    fight, _a, _b = stat_fight(EffectSpec(specs.DECREASE_STATS, base=4, stats=("Strength",)))
    before = copy.deepcopy(fight)
    events = play(fight, [100, 0])
    apply_events(before, RULES, events)
    assert dehydrate(before) == dehydrate(fight)


# --- the log and the snapshot ---------------------------------------------------------------------------------------------------------------

def busy():
    venom = StatusSpec("venom", "Venom", BAD, 3, Curve(status.FALLING), (Tick(status.ROUND_END, status.DAMAGE, amount=3, attribute="poison"),),
                       xp_share=0.1)
    regen = StatusSpec("regen", "Regen", GOOD, None, ticks=(Tick(status.TURN_END, status.HEAL, amount=2, every=2),))
    lull = StatusSpec("lull", "Lull", BAD, 4, ticks=(Tick(status.TURN_START, status.SKIP_TURN, chance=40), Tick(status.HARMED, status.END)),
                      modifiers=(Modifier(status.STAT, stat="Speed", amount=-3),))
    a, b = fighter("A", HP=60, Speed=12), fighter("B", HP=60, Speed=10)
    a.inventory = [[sword(), 1], [item("fang", EffectSpec(specs.CAUSE_BAD_STATUS, status="venom")), 3],
                   [item("lullaby", EffectSpec(specs.CAUSE_BAD_STATUS, status="lull")), 3],
                   [item("curse", EffectSpec(specs.DECREASE_STATS, base=3, added=2, stats=("Strength", "Dodge"))), 3]]
    a.equipment = {"lhand": 0}
    b.inventory, b.equipment = [[sword(), 1]], {"lhand": 0}
    fight = build_fight({0: {0: [a]}, 1: {0: [b]}}, {each.key: each for each in (venom, regen, lull)})
    bear(a, regen, source=A)
    return fight, a, b


def test_every_round_with_statuses_in_it_replays_to_the_same_fight():
    live, a, b = busy()
    replayed = copy.deepcopy(live)
    for number in range(1, 9):
        for fight in (live, replayed):  # the commands are part of the state, so both are given them
            fight.get(A).command, fight.get(A).using, fight.get(A).target = (Command.ITEM, 1 + number % 3, B) if number % 2 else (Command.ATTACK_LEFT, 0, B)
            fight.get(B).command, fight.get(B).target = Command.ATTACK_LEFT, A
        events = do_combat(live, RULES, fight_stream(WorldRng(99), "hub", 1, number))
        stored = [Event.from_list(json.loads(json.dumps(each.to_list()))) for each in events]
        apply_events(replayed, RULES, stored)
        assert dehydrate(replayed) == dehydrate(live), f"round {number}"
        if live.live_parties(RULES) < 2:
            break
    assert number >= 3


def test_the_snapshot_keeps_the_statuses_and_their_tokens_through_json():
    fight, a, b = busy()
    bear(b, fight.statuses["venom"], source=A, rounds=1)
    raw = json.loads(json.dumps(dehydrate(fight)))
    again = hydrate(raw)
    assert dehydrate(again) == dehydrate(fight)
    token = again.get(B).tokens[0]
    assert (token.spec.key, token.source, token.rounds, token.remaining) == ("venom", A, 1, 2)
    assert again.statuses["venom"].curve.shape == status.FALLING and again.statuses["venom"].ticks[0].attribute == "poison"


def test_a_fight_stored_before_statuses_existed_still_loads():
    raw = json.loads(json.dumps(dehydrate(busy()[0])))
    del raw["statuses"]
    for party in raw["parties"]:
        for group in party["groups"]:
            for each in group["characters"]:
                each["tokens"] = []
                for ability in each["abilities"]:
                    for key in ("stats", "status", "duration"):
                        ability["effect"].pop(key)
    assert hydrate(raw).statuses == {}


def test_no_statuses_means_no_new_events_in_a_round():
    a, b = fighter("A"), fighter("B")
    a.inventory, a.equipment = [[sword(), 1]], {"lhand": 0}
    a.command, a.target = Command.ATTACK_LEFT, B
    kinds = types(play(build_fight({0: {0: [a]}, 1: {0: [b]}}), [100, 50]))
    assert not {EventType.ROUND_END, EventType.STATUS_TICK, EventType.STATUS_APPLIED, EventType.STATUS_REMOVED} & set(kinds)


# --- the seed format ----------------------------------------------------------------------------------------------------------------------------

STATUS = {
    "key": "poison", "name": "Poison", "kind": "bad", "duration": 3, "intensity": {"shape": "falling", "high": 1.0, "low": 0.25},
    "ticks": [{"when": "round_end", "action": "damage", "amount": 4, "attribute": "poison"}, {"when": "harmed", "action": "end"}],
    "modifiers": [{"kind": "damage_taken", "attribute": "fire", "factor": 1.5}, {"kind": "stat", "stat": "Speed", "amount": -2}],
    "xp_share": 0.1,
}


def seed(**changes):
    return {"statuses": [{**STATUS, **changes}]}


def refused(data, text):
    with pytest.raises(ContentError) as caught:
        check_seed(data)
    assert text in str(caught.value), str(caught.value)


def test_a_status_file_is_checked_and_its_defaults_filled_in():
    row = check_seed(seed())["statuses"][0]
    assert (row.kind, row.duration, row.intensity.low, row.ticks[0].every, row.ticks[0].chance) == ("bad", 3, 0.25, 1, 100)
    plain = check_seed({"statuses": [{"key": "mark", "name": "Mark", "kind": "good"}]})["statuses"][0]
    assert (plain.duration, plain.intensity.shape, plain.ticks, plain.modifiers, plain.xp_share) == (None, "flat", [], [], 0.0)


@pytest.mark.parametrize("changes, text", [
    ({"kind": "neutral"}, "kind"),
    ({"duration": 0}, "duration"),
    ({"duration": None}, "intensity must be flat"),
    ({"intensity": {"shape": "falling", "high": 0.5, "low": 1.0}}, "low can't be above high"),
    ({"xp_share": 2}, "xp_share"),
    ({"ticks": [{"when": "someday", "action": "damage", "amount": 1}]}, "when"),
    ({"ticks": [{"when": "round_end", "action": "explode"}]}, "action"),
    ({"ticks": [{"when": "round_end", "action": "damage"}]}, "needs an amount or a percent"),
    ({"ticks": [{"when": "round_end", "action": "end", "amount": 2}]}, "only for damage and heal"),
    ({"ticks": [{"when": "round_end", "action": "skip_turn"}]}, "skip_turn is only for turn_start"),
    ({"ticks": [{"when": "turn_start", "action": "end", "chance": 50}]}, "chance is only for skip_turn"),
    ({"ticks": [{"when": "harmed", "action": "end", "every": 2}]}, "every is only for"),
    ({"ticks": [{"when": "round_end", "action": "heal", "amount": 1, "resource": "Strength"}]}, "not a resource"),
    ({"modifiers": [{"kind": "stat", "stat": "HP", "amount": 1}]}, "not a stat that can be raised"),
    ({"modifiers": [{"kind": "stat", "stat": "Luck", "amount": 1}]}, "not a stat that can be raised"),
    ({"modifiers": [{"kind": "stat", "stat": "Speed", "factor": 2.0}]}, "for the damage modifiers"),
    ({"modifiers": [{"kind": "damage_taken", "stat": "Speed"}]}, "for the stat modifier"),
    ({"surprise": 1}, "surprise"),
])
def test_a_bad_status_is_refused_with_what_is_wrong(changes, text):
    refused(seed(**changes), text)


def test_a_game_with_its_own_stats_and_resources_gets_its_own_checks():
    data = {"statuses": [{"key": "rage", "name": "Rage", "kind": "good", "modifiers": [{"kind": "stat", "stat": "Fury", "amount": 3}],
                          "ticks": [{"when": "round_end", "action": "heal", "amount": 1, "resource": "Focus"}]}]}
    with pytest.raises(ContentError):
        check_seed(data)
    assert check_seed(data, (*Rules.stats, "Fury"), ("HP", "MP", "Focus"))["statuses"][0].key == "rage"


def effect_in_ability(**effect):
    return {"abilities": [{"key": "a", "name": "A", "kind": "spell", "effect": effect}], **{"statuses": [STATUS]}}


def test_effects_check_their_stats_and_statuses():
    ok = check_seed(effect_in_ability(effect="cause_bad_status", status="poison", duration=2))["abilities"][0].effect
    assert (ok.status, ok.duration) == ("poison", 2)
    assert check_seed(effect_in_ability(effect="decrease_stats", stats=["Strength", "Dodge"]))["abilities"][0].effect.stats == ["Strength", "Dodge"]
    assert check_seed(effect_in_ability(effect="remove_bad_status"))["abilities"][0].effect.status is None
    refused(effect_in_ability(effect="decrease_stats"), "needs stats")
    refused(effect_in_ability(effect="decrease_stats", stats=["HP"]), "not a stat that can be pushed")
    refused(effect_in_ability(effect="decrease_stats", stats=["Luck"]), "not a stat that can be pushed")
    refused(effect_in_ability(effect="hurt", stats=["Strength"]), "stats is only for")
    refused(effect_in_ability(effect="cause_bad_status"), "needs a status")
    refused(effect_in_ability(effect="hurt", status="poison"), "only for the status effects")
    refused(effect_in_ability(effect="remove_bad_status", duration=2), "only for the effects that place")
    refused(effect_in_ability(effect="cause_bad_status", status="nope"), "names 'nope', which statuses.json doesn't have")
    refused(effect_in_ability(effect="cause_good_status", status="poison"), "which is a bad status")
    refused(effect_in_ability(effect="remove_good_status", status="poison"), "which is a bad status")


def test_an_item_s_use_effect_can_name_a_status_too():
    data = {"items": [{"key": "i", "name": "I", "use_effect": {"effect": "cause_bad_status", "status": "poison"}}], "statuses": [STATUS]}
    assert check_seed(data)["items"][0].use_effect.status == "poison"
    refused({"items": data["items"]}, "statuses.json doesn't have")


def test_a_repeated_status_key_is_refused():
    refused({"statuses": [STATUS, STATUS]}, "statuses.json: key 'poison' is used twice")


# --- in the database ---------------------------------------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_loading_puts_statuses_in_the_database_and_a_dropped_one_goes_inactive(db):
    counts = await load_content(db, seed())
    await db.commit()
    assert counts == {"statuses": 1}
    row = await db.scalar(select(models.Status))
    assert (row.key, row.kind, row.duration, row.xp_share, row.active) == ("poison", "bad", 3, 0.1, True)
    assert row.intensity == {"shape": "falling", "high": 1.0, "low": 0.25}
    assert row.ticks[0]["when"] == "round_end" and row.ticks[0]["amount"] == 4 and row.modifiers[1] == {
        "kind": "stat", "attribute": "all", "factor": 1.0, "stat": "Speed", "amount": -2}
    spec = status_spec(row)
    assert spec.curve == Curve(status.FALLING, 1.0, 0.25) and spec.ticks[0] == Tick(status.ROUND_END, status.DAMAGE, amount=4, attribute="poison")
    assert spec.modifiers[0] == Modifier(status.DAMAGE_TAKEN, "fire", 1.5) and spec.duration == 3 and spec.xp_share == 0.1
    assert (await known_statuses(db)) == {"poison": spec}
    await load_content(db, {"statuses": []})
    await db.commit()
    assert (await db.scalar(select(models.Status))).active is False
    assert await known_statuses(db) == {}, "a dropped status is not handed to new fights"


@pytest.mark.anyio
async def test_a_fight_with_statuses_is_stored_replayed_and_verified(db):
    hub = await ensure_start(db)
    venom = StatusSpec("venom", "Venom", BAD, 3, Curve(status.FALLING), (Tick(status.ROUND_END, status.DAMAGE, amount=6),))
    a, b = fighter("A", HP=50), fighter("B", HP=50)
    bear(b, venom, source=A)
    record = await store.create_fight(db, hub, build_fight({0: {0: [a]}, 1: {0: [b]}}, {"venom": venom}))
    assert record.initial_state["statuses"]["venom"]["curve"]["shape"] == "falling"
    for _ in range(3):
        await store.play_round(db, record, [], RULES, snapshot=True)
    fight, rounds = await store.load_state(db, record, RULES)
    assert rounds == 3 and fight.get(B).tokens == [] and fight.get(B).current["HP"] == 50 - 6 - 3, "6 at full strength, 3 at half, and nothing on the last round"
    assert await store.verify(db, record, RULES, deep=True) == 3
