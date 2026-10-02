"""The fight rules and a round's resolution: DragonStar's rules, kept as they are, with the dice under control.

Pure code, so these need no database. A ``Scripted`` stream hands out exactly the rolls a test chooses,
so every expected number below can be checked by hand against the rules.
"""

import copy
import json
import random

import pytest

from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import specs
from terraforma.fights.combatant import Combatant, Command
from terraforma.fights.events import Event, EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.gear import EquipOutcome
from terraforma.fights.replay import apply_events
from terraforma.fights.resolve import do_combat, fight_stream, participants
from terraforma.fights.rules import Resource, Rules
from terraforma.fights.specs import AbilitySpec, EffectSpec, ItemSpec
from terraforma.world.rng import WorldRng

RULES = Rules()


class Scripted:
    """A random stream that returns the rolls it was given, in order, and checks each is in range."""

    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randint(self, low, high):
        roll = self.rolls.pop(0)
        assert low <= roll <= high, f"scripted roll {roll} is outside {low}..{high}"
        return roll

    def shuffle(self, items):
        pass

    def choice(self, items):
        return items[0]


def fighter(name="Fighter", **stats) -> Combatant:
    base = {stat: 0 for stat in RULES.stats}
    base.update(HP=20, MP=10, Speed=10, Accuracy=10, Strength=10, Dodge=1, Block=1, Power=10, Resistance=1, Focus=10)
    base.update(stats)
    return Combatant(name, base, dict(base))


def types(events):
    return [each.type for each in events]


def listing(events):
    return [(each.type.value, *each.data) for each in events]


def duel(attacker=None, defender=None):
    attacker, defender = attacker or fighter("A"), defender or fighter("B")
    return build_fight({0: {0: [attacker]}, 1: {0: [defender]}}), attacker, defender


def sword(**changes) -> ItemSpec:
    return ItemSpec(key="sword", name="Sword", equip_slots=("hand",), **changes)


# --- the formulas ---------------------------------------------------------------------------------

def test_a_hit_does_damage_by_strength_against_block_and_the_quality_of_the_roll():
    assert RULES.hit_damage(10, 10, 10, 10, False, 1.0) == 2, "a barely-hit even match"
    assert RULES.hit_damage(10, 10, 10, 5, False, 1.0) == 4
    assert RULES.hit_damage(20, 10, 10, 10, False, 1.0) == 7


def test_a_critical_doubles_strength():
    assert RULES.hit_damage(10, 10, 10, 1, False, 1.0, critical=True) == 14  # as if Strength were 20
    assert RULES.hit_damage(10, 10, 10, 1, False, 1.0, critical=True) > RULES.hit_damage(10, 10, 10, 1, False, 1.0)


def test_defending_halves_damage_and_damage_is_never_less_than_one():
    assert RULES.hit_damage(10, 10, 10, 1, True, 1.0, critical=True) == 7
    assert RULES.hit_damage(1, 100, 10, 10, False, 1.0) == 1
    assert RULES.hit_damage(1, 100, 10, 10, True, 1.0) == 1


def test_a_cleaner_hit_does_more_damage_across_the_whole_hit_window():
    window = 60.0
    damages = [RULES.hit_damage(100, 100, window, roll, False, 1.0) for roll in (1, 20, 40, 60)]
    assert damages == sorted(damages, reverse=True) and damages[0] > damages[-1]
    assert damages[-1] >= damages[0] // 2 - 1, "even the edge of the window does about half"


def test_a_ranged_hit_falls_off_with_distance_from_the_centre():
    centre = RULES.hit_damage(20, 10, 10, 1, False, 1.0)
    side = RULES.hit_damage(20, 10, 10, 1, False, 0.5)
    assert side < centre, "a neighbour at half intensity takes less"


def test_accuracy_and_dodge_are_floored_at_one_so_nothing_divides_by_zero():
    assert RULES.hit_chance(0, 0) == 50.0
    assert RULES.chance_to_hit(Scripted(50), 0, 0) == 50 and RULES.chance_to_hit(Scripted(51), 0, 0) is None


def test_the_hit_chance_is_accuracy_out_of_accuracy_plus_dodge_as_a_percentage():
    assert RULES.hit_chance(10, 10) == 50.0
    assert RULES.hit_chance(30, 10) == 75.0
    assert abs(RULES.hit_chance(10, 20) - 100 / 3) < 1e-9


def test_the_roll_is_always_out_of_100_and_hits_up_to_the_chance():
    assert RULES.chance_to_hit(Scripted(50), 10, 10) == 50, "a roll up to the chance hits"
    assert RULES.chance_to_hit(Scripted(51), 10, 10) is None, "above it misses"
    assert RULES.chance_to_hit(Scripted(75), 30, 10) == 75 and RULES.chance_to_hit(Scripted(76), 30, 10) is None
    assert RULES.chance_to_hit(Scripted(1), 1, 5000) is None, "a hopeless attack can miss even a 1"
    assert RULES.chance_to_hit(Scripted(99), 5000, 1) == 99, "nearly a sure thing"
    assert RULES.chance_to_hit(Scripted(100), 5000, 1) is None, "but Dodge counts for at least 1, so never quite"


@pytest.mark.parametrize("accuracy, dodge", [(1, 1), (10, 10), (50, 20), (1000, 1000), (5000, 20), (300, 2000)])
def test_a_critical_is_one_hit_in_a_hundred_whatever_the_size_of_the_stats(accuracy, dodge):
    """Rolling every possible roll once: the critical (a 1) is one of the hundred, hit chance aside."""
    hits = [roll for roll in range(1, 101) if RULES.chance_to_hit(Scripted(roll), accuracy, dodge) is not None]
    assert hits and hits[0] == 1, "the best roll always hits, as long as there is any chance"
    assert [roll for roll in range(1, 101) if RULES.is_critical(roll)] == [1]
    assert RULES.is_critical(1) and not RULES.is_critical(2) and not RULES.is_critical(100)


def test_the_saving_throw_gives_none_half_or_all():
    results = [RULES.saving_throw(Scripted(roll), 10, 10) for roll in (1, 10, 11, 15, 16, 20)]
    assert results == [1.0, 1.0, 0.5, 0.5, 0.0, 0.0]


def test_speed_is_spread_by_fifteen_percent_either_way():
    assert RULES.randomize(Scripted(85), 100) == 85 and RULES.randomize(Scripted(115), 100) == 115
    assert RULES.randomize(Scripted(100), 7) == 7


def test_casting_slows_the_caster_less_with_more_focus():
    spell = AbilitySpec("fire", "Fire", "spell", mp_cost=3)
    assert RULES.speed(Scripted(100), 20) == 20
    assert RULES.speed(Scripted(100, 100), 20, spell, focus=16) == 13, "20 less a third, rounded"
    assert RULES.speed(Scripted(100, 100), 20, spell, focus=400) > 13, "more Focus, less slowing"


def test_php_rounding_goes_away_from_zero():
    from terraforma.fights.gear import round_half_up

    assert [round_half_up(value) for value in (2.5, -2.5, 2.4, 0.5)] == [3, -3, 2, 1]


# --- the order ----------------------------------------------------------------------------------------

def test_the_fastest_acts_first_and_defenders_and_the_dead_do_not_act():
    slow, quick, dead, guard = fighter("slow", Speed=5), fighter("quick", Speed=20), fighter("dead"), fighter("guard")
    dead.current["HP"] = 0
    guard.command = Command.DEFEND
    for each in (slow, quick, dead):
        each.command = Command.RUN
    fight = build_fight({0: {0: [slow, dead]}, 1: {0: [quick, guard]}})
    order = participants(fight, RULES, Scripted(*[100] * 4))
    assert order == [(1, 0, 0), (0, 0, 0)]


def test_a_weapon_that_strikes_several_times_appears_several_times():
    hero = fighter("hero", Speed=30)
    hero.inventory = [[sword(attack_count=3), 1]]
    hero.equipment = {"lhand": 0}
    hero.command = Command.ATTACK_LEFT
    other = fighter("other", Speed=15)
    other.command = Command.RUN
    fight = build_fight({0: {0: [hero]}, 1: {0: [other]}})
    order = participants(fight, RULES, Scripted(100, 100))
    # Spaced through its speed: at 10, 20 and 30.
    assert order == [(0, 0, 0), (0, 0, 0), (1, 0, 0), (0, 0, 0)]
    assert [fight.get(address).name for address in order] == ["hero", "hero", "other", "hero"]


def test_fighters_with_the_same_speed_go_in_whatever_order_the_stream_shuffles_them():
    class Reversing(Scripted):
        def shuffle(self, items):
            items.reverse()

    def order(rng):
        first, second = fighter("a", Speed=10), fighter("b", Speed=10)
        for each in (first, second):
            each.command = Command.RUN
        return participants(build_fight({0: {0: [first]}, 1: {0: [second]}}), RULES, rng)

    assert order(Scripted(100, 100)) == [(0, 0, 0), (1, 0, 0)]
    assert order(Reversing(100, 100)) == [(1, 0, 0), (0, 0, 0)]


# --- a round -----------------------------------------------------------------------------------------------

def test_a_blow_that_lands_is_a_turn_an_attack_a_target_and_damage():
    attacker, defender = fighter("A", Accuracy=10, Strength=10, Dodge=1), fighter("B", Dodge=10, Block=10)
    fight, attacker, defender = duel(attacker, defender)
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
    defender.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 10))  # Speed roll, then a hit roll of 10
    # A 50% chance and a roll of 10: hit_damage(10, 10, 50, 10) is 4, halved by defending.
    assert listing(events) == [
        ("Turn", 0, 0, 0), ("Attack", None, "left", 0), ("Target", 1, 0, 0, 0), ("Damage", 1, 0, 0, 2, False),
        ("XpDebt", 1, 0, 0, 0, 0, 0, 0.1, RULES.pxp(defender)),  # 2 of 20 life: it owes the attacker for it
    ]
    assert defender.current["HP"] == 18


def test_a_miss_is_reported_and_does_no_damage():
    fight, attacker, defender = duel(fighter("A", Accuracy=5), fighter("B", Dodge=5))
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
    defender.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 90))  # a 50% chance, and a roll of 90
    assert types(events)[-1] is EventType.MISS and defender.current["HP"] == 20


def test_a_critical_is_flagged_and_a_killing_blow_says_how_far_past_zero():
    fight, attacker, defender = duel(fighter("A", Strength=40, Accuracy=10), fighter("B", HP=5, Block=1, Dodge=10))
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
    defender.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 1))
    damage = [each for each in events if each.type is EventType.DAMAGE][0]
    assert damage.data[4] is True, "a roll of 1 is a critical"
    died = [each for each in events if each.type is EventType.DIED][0]
    assert died.data[4] == damage.data[3] - 5, "overkill is the damage past the 5 life it had"
    assert defender.current["HP"] == 0


def test_the_round_stops_when_one_side_is_all_that_is_left():
    killer = fighter("killer", Strength=100, Accuracy=10, Speed=50)
    victim = fighter("victim", HP=1, Block=1, Dodge=1)
    fight = build_fight({0: {0: [killer]}, 1: {0: [victim]}})
    killer.command, killer.target = Command.ATTACK_LEFT, (1, 0, 0)
    victim.command, victim.target = Command.ATTACK_LEFT, (0, 0, 0)
    events = do_combat(fight, RULES, Scripted(100, 100, 1))
    assert len([each for each in events if each.type is EventType.TURN]) == 1, "the victim never gets a turn"


def test_defending_halves_what_a_hit_does():
    hits = []
    for guarding in (False, True):
        fight, attacker, defender = duel(fighter("A", Strength=20, Accuracy=10), fighter("B", Dodge=10, Block=10, HP=100))
        attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
        defender.command = Command.DEFEND if guarding else Command.RUN
        do_combat(fight, RULES, Scripted(100, 10) if guarding else Scripted(100, 100, 10))
        hits.append(100 - defender.current["HP"])
    assert hits[1] == hits[0] // 2


def test_a_weapon_in_the_hand_is_what_strikes_and_a_two_handed_one_leaves_the_right_hand_idle():
    hero = fighter("hero", Strength=10, Accuracy=10)
    great = ItemSpec("great", "Greatsword", ("lhand", "rhand"), stat_bonus={"Strength": 10})
    hero.inventory = [[great, 1]]
    hero.equipment = {"lhand": 0, "rhand": 0}
    assert hero.weapon(RULES, 0) is great and hero.weapon(RULES, 1) is None
    hero.command, hero.target = Command.ATTACK_RIGHT, (1, 0, 0)
    fight, hero, foe = duel(hero, fighter("foe", HP=50))
    events = do_combat(fight, RULES, Scripted(100))
    assert listing(events) == [("Turn", 0, 0, 0)], "the right hand of a two-handed weapon has nothing to swing"


def test_gear_adds_to_stats_only_for_the_hand_that_acts():
    hero = fighter("hero", Strength=10)
    left = sword(stat_bonus={"Strength": 5})
    right = ItemSpec("dagger", "Dagger", ("hand",), stat_bonus={"Strength": 3})
    hero.inventory = [[left, 1], [right, 1]]
    hero.equipment = {"lhand": 0, "rhand": 1}
    assert hero.get_current(RULES, "Strength", Command.ATTACK_LEFT) == 15
    assert hero.get_current(RULES, "Strength", Command.ATTACK_RIGHT) == 13
    assert hero.get_current(RULES, "Strength", Command.SPELL) == 10, "casting counts neither hand"
    assert hero.get_current(RULES, "Strength", True) == 18, "everything"


def test_a_ranged_weapon_hits_neighbours_for_less_and_each_gets_its_own_roll():
    attacker = fighter("A", Strength=20, Accuracy=10)
    attacker.inventory = [[sword(attack_targets=1), 1]]
    attacker.equipment = {"lhand": 0}
    foes = [fighter(f"foe{n}", HP=100, Dodge=10, Block=10) for n in range(3)]
    fight = build_fight({0: {0: [attacker]}, 1: {0: foes}})
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 1)
    for foe in foes:
        foe.command = Command.RUN
    events = do_combat(fight, RULES, Scripted(100, 100, 100, 100, 1, 1, 1))
    hits = {each.data[2]: each.data[3] for each in events if each.type is EventType.DAMAGE}
    assert set(hits) == {0, 1, 2}
    assert hits[1] > hits[0] and hits[1] > hits[2], "the middle one, the target, takes the most"
    assert [each.type for each in events].count(EventType.MISS) == 0


# --- skills, spells and items ----------------------------------------------------------------------------------

def spell(effect, cost=3, kind="spell") -> AbilitySpec:
    return AbilitySpec("magic", "Magic", kind, cost, effect)


def cast(ability, rolls, target=(1, 0, 0), caster=None, foe=None):
    caster = caster or fighter("caster", Power=20)
    foe = foe or fighter("foe", HP=50, Resistance=10)
    fight = build_fight({0: {0: [caster]}, 1: {0: [foe]}})
    caster.abilities = [ability]
    caster.command = Command.SPELL if ability.kind == "spell" else Command.SKILL
    caster.using, caster.target = 0, target
    foe.command = Command.RUN
    return fight, caster, foe, do_combat(fight, RULES, Scripted(*rolls))


def test_a_spell_costs_mp_and_hurts_for_base_plus_some_scaled_by_the_save():
    fire = spell(EffectSpec(specs.HURT, 0, base=10, added=4))
    # Speed rolls (the caster's two, the foe's one), then the saving throw (1: all gets through), then the amount (4 of up to 4).
    fight, caster, foe, events = cast(fire, [100, 100, 100, 1, 4])
    assert caster.current["MP"] == 7
    # The foe, who is only running, is quicker than a caster slowed by the spell, so goes first.
    assert listing(events)[:6] == [
        ("Turn", 1, 0, 0), ("Run",),
        ("Turn", 0, 0, 0), ("Spell", "magic"), ("Target", 1, 0, 0, 0), ("AlterStat", 0, 0, 0, "MP", -3),
    ]
    assert foe.current["HP"] == 36, "10 + 4 damage, all of it"


def test_a_resisted_spell_does_half_or_nothing():
    fire = spell(EffectSpec(specs.HURT, 0, base=10, added=0))
    _fight, _caster, foe, _events = cast(fire, [100, 100, 100, 23, 0])  # above the caster's Power of 20, within 25: half
    assert foe.current["HP"] == 45
    _fight, _caster, foe, events = cast(fire, [100, 100, 100, 30])  # above Power plus half Resistance (25): resisted
    assert foe.current["HP"] == 50 and types(events)[-1] is EventType.NO_EFFECT


def test_a_spell_with_not_enough_mp_does_nothing_but_says_so():
    fire = spell(EffectSpec(specs.HURT, 0, base=10), cost=50)
    _fight, caster, foe, events = cast(fire, [100, 100, 100])
    assert types(events)[-1] is EventType.NO_MP and caster.current["MP"] == 10 and foe.current["HP"] == 50


def test_a_harmful_skill_can_be_dodged_but_not_resisted():
    stab = spell(EffectSpec(specs.HURT, 0, base=8), cost=0, kind="skill")
    _fight, _caster, foe, events = cast(stab, [100, 100, 100, 70], foe=fighter("foe", HP=50, Dodge=10))
    assert types(events)[-1] is EventType.MISS and foe.current["HP"] == 50
    _fight, _caster, foe, events = cast(stab, [100, 100, 100, 5, 0], foe=fighter("foe", HP=50, Dodge=10))
    assert foe.current["HP"] == 42


def test_helping_is_never_dodged_or_resisted_and_heals_up_to_the_maximum():
    heal = spell(EffectSpec(specs.HEAL, 0, base=30), cost=0)
    foe = fighter("friend", HP=50)
    foe.current["HP"] = 40
    _fight, _caster, foe, events = cast(heal, [100, 100, 100, 0], foe=foe)
    assert foe.current["HP"] == 50, "only 10 of the 30 fit"
    assert ("Restore", 1, 0, 0, "HP", 10) in listing(events)


def test_a_whole_group_can_be_reached_at_once():
    blast = spell(EffectSpec(specs.HURT, specs.GROUP, base=5), cost=0, kind="skill")
    foes = [fighter(f"foe{n}", HP=20, Dodge=1) for n in range(3)]
    caster = fighter("caster")
    fight = build_fight({0: {0: [caster]}, 1: {0: foes}})
    caster.abilities, caster.command, caster.using, caster.target = [blast], Command.SKILL, 0, (1, 0, 0)
    for foe in foes:
        foe.command = Command.RUN
    do_combat(fight, RULES, Scripted(100, 100, 100, 100, 100, 1, 0, 1, 0, 1, 0))
    assert [foe.current["HP"] for foe in foes] == [15, 15, 15]


def test_restoring_mp():
    refill = spell(EffectSpec(specs.RESTORE_MP, 0, base=6), cost=0)
    ally = fighter("ally", MP=10)
    ally.current["MP"] = 2
    _fight, _caster, ally, _events = cast(refill, [100, 100, 100, 0], foe=ally)
    assert ally.current["MP"] == 8


def test_reviving_a_fallen_friend_brings_them_back_at_full_life():
    revive = spell(EffectSpec(specs.REVIVE, 0, base=100, added=50), cost=0)
    caster, fallen, foe = fighter("caster"), fighter("fallen", HP=30), fighter("foe")
    fallen.current["HP"] = 0
    fight = build_fight({0: {0: [caster, fallen]}, 1: {0: [foe]}})
    caster.abilities, caster.command, caster.using, caster.target = [revive], Command.SPELL, 0, (0, 0, 1)
    foe.command = Command.DEFEND
    events = do_combat(fight, RULES, Scripted(100, 100))
    assert fallen.current["HP"] == 30 and ("Revived", 0, 0, 1) in listing(events)


def test_an_item_is_used_up_when_it_is_one_use_and_kept_when_it_is_not():
    potion = ItemSpec("potion", "Potion", one_use=True, use_effect=EffectSpec(specs.HEAL, 0, base=10))
    tonic = ItemSpec("tonic", "Tonic", one_use=False, use_effect=EffectSpec(specs.HEAL, 0, base=10))
    for item, kept in ((potion, 0), (tonic, 1)):
        user = fighter("user")
        user.inventory = [[item, 1]]
        user.current["HP"] = 5
        user.command, user.using, user.target = Command.ITEM, 0, (0, 0, 0)
        fight = build_fight({0: {0: [user]}, 1: {0: [fighter("other")]}})
        fight.get((1, 0, 0)).command = Command.RUN
        events = do_combat(fight, RULES, Scripted(100, 100, 0))
        assert user.current["HP"] == 15 and len(user.inventory) == kept
        assert (EventType.USE_ITEM in types(events)) == (kept == 0)


# --- ammunition ---------------------------------------------------------------------------------------------------

def archer(ammo_count):
    hero = fighter("archer", Strength=20, Accuracy=10)
    bow = ItemSpec("bow", "Bow", ("hand",), ammo_type="arrow")
    arrow = ItemSpec("arrow", "Arrow", ("ammo",), ammo_type="arrow")
    hero.inventory = [[bow, 1], [arrow, ammo_count]]
    hero.equipment = {"lhand": 0, "lammo": 1}
    return hero


def test_shooting_spends_one_arrow_and_the_last_one_empties_the_stack():
    hero = archer(2)
    fight, hero, foe = duel(hero, fighter("foe", HP=100, Dodge=1))
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)
    foe.command = Command.RUN
    events = do_combat(fight, RULES, Scripted(100, 100, 2))
    assert hero.inventory[1][1] == 1 and ("ExpendAmmo", 1) in listing(events)
    do_combat(fight, RULES, Scripted(100, 100, 2))
    assert len(hero.inventory) == 1 and hero.equipment["lammo"] is None, "the empty stack is gone and un-equipped"


def test_out_of_ammunition_the_bow_does_nothing():
    hero = archer(1)
    fight, hero, foe = duel(hero, fighter("foe", HP=100))
    hero.inventory[1][1] = 0
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)
    foe.command = Command.RUN
    events = do_combat(fight, RULES, Scripted(100, 100))
    assert types(events)[:2] == [EventType.TURN, EventType.NO_AMMO] and foe.current["HP"] == 100


def test_ammunition_sets_the_range_and_kind_of_the_attack():
    hero = archer(3)
    hero.inventory[1][0] = ItemSpec("fire arrow", "Fire arrow", ("ammo",), ammo_type="arrow", attack_targets=2, attack_attribute="fire")
    assert hero.weapon_effect(RULES, Command.ATTACK_LEFT) == EffectSpec(specs.HURT, 2, 10, 10, "fire")


# --- targets that are gone --------------------------------------------------------------------------------------------

def test_a_target_that_died_is_swapped_for_a_living_neighbour():
    hero = fighter("hero", Strength=30, Accuracy=10)
    dead, alive = fighter("dead", HP=10), fighter("alive", HP=100, Dodge=1)
    dead.current["HP"] = 0
    fight = build_fight({0: {0: [hero]}, 1: {0: [dead, alive]}})
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)
    alive.command = Command.RUN
    events = do_combat(fight, RULES, Scripted(100, 100, 5))
    assert ("Target", 1, 0, 1, 0) in listing(events) and alive.current["HP"] < 100


def test_nothing_to_hit_means_no_action_at_all():
    hero = fighter("hero")
    foes = [fighter("a"), fighter("b")]
    for foe in foes:
        foe.current["HP"] = 0
    fight = build_fight({0: {0: [hero]}, 1: {0: foes[:1]}, 2: {0: foes[1:]}})
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)
    assert do_combat(fight, RULES, Scripted(100)) == [], "only one party has anyone alive: the fight is already over"


# --- changing gear in a fight ---------------------------------------------------------------------------------------------

def test_changing_weapons_takes_the_old_one_off_first_and_says_what_changed():
    hero = fighter("hero")
    old, new = sword(), ItemSpec("axe", "Axe", ("hand",))
    hero.inventory = [[old, 1], [new, 1]]
    hero.equipment = {"lhand": 0}
    hero.command, hero.using, hero.target = Command.EQUIP, 1, (0, 0, 0)
    fight, hero, foe = duel(hero)
    events = do_combat(fight, RULES, Scripted(100))
    assert listing(events) == [("Turn", 0, 0, 0), ("UnequipSlot", "lhand"), ("EquipSlot", 1, "lhand"), ("Equip", "axe")]
    assert hero.equipment["lhand"] == 1


def test_putting_a_weapon_away_and_loading_ammunition():
    hero = archer(5)
    hero.equipment = {}
    hero.command, hero.using, hero.target = Command.EQUIP_AMMO, 0, (0, 1, 0)
    fight, hero, foe = duel(hero)
    events = do_combat(fight, RULES, Scripted(100))
    assert listing(events) == [("Turn", 0, 0, 0), ("EquipSlot", 0, "lhand"), ("EquipSlot", 1, "lammo"), ("Equip", "bow", "arrow")]
    hero.command, hero.using, hero.target = Command.EQUIP, -1, (0, 0, 0)
    events = do_combat(fight, RULES, Scripted(100))
    assert hero.equipment == {"lhand": None, "lammo": None}
    assert [each[0] for each in listing(events)] == ["Turn", "Unequip", "UnequipSlot", "UnequipSlot"]


def test_a_command_that_makes_no_sense_just_passes_the_turn():
    hero = fighter("hero")
    hero.command, hero.using = Command.SPELL, 9
    fight, hero, foe = duel(hero)
    assert listing(do_combat(fight, RULES, Scripted(100))) == [("Turn", 0, 0, 0)]


# --- replay and determinism -----------------------------------------------------------------------------------------------

def busy_fight():
    heroes = [fighter("a", Speed=12, Strength=15), fighter("b", Speed=9, Power=25)]
    heroes[1].abilities = [spell(EffectSpec(specs.HURT, specs.GROUP, base=6, added=6))]
    heroes[0].inventory = [[sword(), 1], [ItemSpec("potion", "Potion", one_use=True, use_effect=EffectSpec(specs.HEAL, 0, base=8)), 2]]
    heroes[0].equipment = {"lhand": 0}
    monsters = [fighter(f"m{n}", HP=40, Speed=8 + n, Dodge=3, Block=4) for n in range(3)]
    return build_fight({0: {0: heroes}, 1: {0: monsters}})


def command_everyone(fight):
    for address in fight.addresses():
        person = fight.get(address)
        if address[0] == 0:
            if address[2] == 0:
                person.command, person.target = Command.ATTACK_LEFT, (1, 0, address[2] + 1)
            else:
                person.command, person.using, person.target = Command.SPELL, 0, (1, 0, 0)
        else:
            person.command, person.target = Command.ATTACK_LEFT, (0, 0, address[2] % 2)


def state(fight):
    return [(fight.get(a).current, fight.get(a).inventory and [(stack[0].key, stack[1]) for stack in fight.get(a).inventory],
             fight.get(a).equipment) for a in fight.addresses()]


@pytest.mark.parametrize("seed", range(8))
def test_replaying_a_rounds_events_on_the_fight_as_it_was_gives_the_same_result(seed):
    fight = busy_fight()
    before = copy.deepcopy(fight)
    command_everyone(fight)
    events = do_combat(fight, RULES, random.Random(seed))
    assert events, "something happened"
    apply_events(before, RULES, events)
    assert state(before) == state(fight)


def test_events_are_plain_lists_that_survive_json():
    fight = busy_fight()
    command_everyone(fight)
    events = do_combat(fight, RULES, random.Random(1))
    again = [Event.from_list(item) for item in json.loads(json.dumps([each.to_list() for each in events]))]
    assert again == events


def test_the_same_fight_with_the_same_stream_plays_out_identically_and_another_map_differs():
    def play(world, map_name):
        fight = busy_fight()
        command_everyone(fight)
        return do_combat(fight, RULES, fight_stream(WorldRng(world), map_name, 7))

    assert play(1234, "hub") == play(1234, "hub")
    assert play(1234, "hub") != play(1234, "forest") or play(1234, "hub") != play(4321, "hub")


# --- the framework a game overrides ---------------------------------------------------------------------------------------------

def test_a_game_can_replace_a_formula():
    class Brutal(Rules):
        def hit_damage(self, strength, block, window, roll, defending, impact, critical=False):
            return 99

        def chance_to_hit(self, rng, accuracy, dodge):
            return 3

    fight, attacker, defender = duel(fighter("A"), fighter("B", HP=500))
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
    defender.command = Command.RUN
    do_combat(fight, Brutal(), Scripted(100, 100))
    assert defender.current["HP"] == 401


def test_a_game_can_define_its_own_stats_and_the_seed_follows():
    class Luckier(Rules):
        stats = (*Rules.stats, "Luck")

    seed = {"abilities": [], "monsters": [], "items": [{"key": "charm", "name": "Charm", "stat_bonus": {"Luck": 3}}]}
    with pytest.raises(ContentError, match="Luck"):
        check_seed(seed)
    checked = check_seed(seed, Luckier.stats)
    assert checked["items"][0].stat_bonus["Luck"] == 3 and checked["items"][0].stat_bonus["HP"] == 0
    assert "Luck" in Luckier.stats and "Luck" not in Rules.stats


def test_a_game_can_have_resources_of_its_own_that_the_fight_treats_as_pools():
    class Rageful(Rules):
        stats = (*Rules.stats, "Rage")
        resources = (*Rules.resources, Resource("Rage"))

        def gauge_moved(self, fight, actor, target, resource, before, after, maximum):
            if resource == "HP" and after < before:
                fight.get(target).current["Rage"] = min(100, fight.get(target).current["Rage"] + (before - after))
            return []

    rules = Rageful()
    assert rules.resource_names == ("HP", "MP", "Rage") and rules.vital == "HP"
    attacker, defender = fighter("A", Rage=0, Accuracy=10, Strength=20), fighter("B", Rage=100, HP=50, Dodge=1)
    defender.current["Rage"] = 0
    fight, attacker, defender = duel(attacker, defender)
    attacker.command, attacker.target = Command.ATTACK_LEFT, (1, 0, 0)
    defender.command = Command.RUN
    do_combat(fight, rules, Scripted(100, 100, 1))
    assert defender.current["Rage"] == 50 - defender.current["HP"] > 0, "being hurt filled the rage pool"
    assert defender.get_base(rules, "Rage") == 100, "a pool's base is its maximum"


def test_the_rules_hear_about_every_event_and_may_add_more():
    class Echo(Rules):
        def after_event(self, fight, event):
            return [Event(EventType.DEFEND)] if event.type is EventType.RUN else []

    fight, hero, foe = duel()
    hero.command = foe.command = Command.RUN
    events = do_combat(fight, Echo(), Scripted(100, 100))
    assert types(events).count(EventType.DEFEND) == 2


def test_seed_targets_may_be_a_range_and_names_turn_into_numbers():
    ok = check_seed({"abilities": [{"key": "a", "name": "A", "kind": "skill", "effect": {"effect": "hurt", "targets": 2}}]})
    assert ok["abilities"][0].effect.targets == 2
    assert EffectSpec.from_dict({"effect": "hurt", "targets": "group"}).targets == specs.GROUP
    with pytest.raises(ContentError, match="targets"):
        check_seed({"abilities": [{"key": "a", "name": "A", "kind": "skill", "effect": {"targets": -1}}]})


def test_the_equip_outcomes_are_the_ones_heroes_use():
    hero = fighter("hero")
    hero.inventory = [[sword(), 1], [sword(), 1]]
    assert hero.equip(0, 0).outcome is EquipOutcome.SUCCESS
    assert hero.equip(1, 0).outcome is EquipOutcome.NEEDS_UNEQUIPPING
    assert hero.equip(5, 0).outcome is EquipOutcome.NOT_FOUND


# --- allies, enemies and neutrals ---------------------------------------------------------------------------------

def four_parties(alignment=None):
    """Parties 0 (the actor's), 1, 2 and 3, one fighter each."""
    fight = build_fight({number: {0: [fighter(f"p{number}")]} for number in range(4)})
    for number, (allies, enemies) in (alignment or {}).items():
        fight.parties[number].allies, fight.parties[number].enemies = allies, enemies
    return fight


def reached(fight, scope, rules=RULES):
    from terraforma.fights.targets import expand

    effect = EffectSpec(specs.HURT, scope, base=1)
    return sorted({address[0] for address, *_ in expand(fight, rules, (0, 0, 0), (1, 0, 0), effect)})


def test_by_default_each_party_is_for_itself_and_every_other_party_is_an_enemy():
    fight = four_parties()
    assert RULES.alignment(fight, 0) == ({0}, {1, 2, 3})
    assert reached(fight, specs.ALL_ALLIES) == [0]
    assert reached(fight, specs.ALL_ENEMIES) == [1, 2, 3]
    assert reached(fight, specs.ALL_PARTIES) == [0, 1, 2, 3]
    assert reached(fight, specs.ALL_NOT_ALLIES) == [1, 2, 3]
    assert reached(fight, specs.ALL_NOT_ENEMIES) == [0]


def test_a_party_in_neither_list_is_neutral_and_the_not_scopes_include_it():
    # Party 0 is allied with 1, hostile to 2, and has no opinion of 3.
    fight = four_parties({0: ({1}, {2})})
    assert RULES.alignment(fight, 0) == ({0, 1}, {2})
    assert reached(fight, specs.ALL_ALLIES) == [0, 1]
    assert reached(fight, specs.ALL_ENEMIES) == [2]
    assert reached(fight, specs.ALL_NOT_ALLIES) == [2, 3], "enemies and neutrals"
    assert reached(fight, specs.ALL_NOT_ENEMIES) == [0, 1, 3], "allies and neutrals"
    assert reached(fight, specs.ALL_PARTIES) == [0, 1, 2, 3]


def test_setting_only_the_allies_makes_everyone_else_an_enemy_and_only_the_enemies_leaves_the_rest_neutral():
    only_allies = four_parties({0: ({1}, None)})
    assert RULES.alignment(only_allies, 0) == ({0, 1}, {2, 3})
    only_enemies = four_parties({0: (None, {2})})
    assert RULES.alignment(only_enemies, 0) == ({0}, {2})
    assert reached(only_enemies, specs.ALL_NOT_ALLIES) == [1, 2, 3]


def test_a_party_is_always_its_own_ally_even_if_listed_as_an_enemy():
    fight = four_parties({0: (set(), {0, 2})})
    assert RULES.alignment(fight, 0) == ({0}, {2})


def test_a_game_can_decide_alignment_its_own_way():
    class Factions(Rules):
        def alignment(self, fight, party):
            side = {0: "red", 1: "red", 2: "blue", 3: "green"}
            allies = {other for other in fight.parties if side[other] == side[party]}
            enemies = {other for other in fight.parties if side[other] == "blue" and side[party] != "blue"}
            return allies, enemies

    fight = four_parties()
    assert reached(fight, specs.ALL_ALLIES, Factions()) == [0, 1]
    assert reached(fight, specs.ALL_ENEMIES, Factions()) == [2]
    assert reached(fight, specs.ALL_NOT_ALLIES, Factions()) == [2, 3]


def test_a_blast_at_everyone_who_is_not_an_ally_spares_allies_and_hits_neutrals():
    blast = AbilitySpec("blast", "Blast", "skill", 0, EffectSpec(specs.HURT, specs.ALL_NOT_ALLIES, base=5))
    fight = four_parties({0: ({1}, {2})})
    caster = fight.get((0, 0, 0))
    caster.abilities, caster.command, caster.using, caster.target = [blast], Command.SKILL, 0, (2, 0, 0)
    for address in fight.addresses():
        if address != (0, 0, 0):
            fight.get(address).command = Command.DEFEND
    # Skills can be dodged: a hit roll each for the two it reaches (the enemy and the neutral), then the amounts.
    do_combat(fight, RULES, Scripted(100, 100, 1, 0, 1, 0))
    assert [fight.get((party, 0, 0)).current["HP"] for party in range(4)] == [20, 20, 15, 15]


def test_the_new_scopes_are_valid_in_a_seed():
    for name in ("all_not_enemies", "all_not_allies", "all_allies", "all_enemies", "all_parties"):
        checked = check_seed({"abilities": [{"key": "a", "name": "A", "kind": "skill", "effect": {"effect": "hurt", "targets": name}}]})
        assert checked["abilities"][0].effect.targets == name
        assert EffectSpec.from_dict({"effect": "hurt", "targets": name}).targets in specs.BY_ALIGNMENT
