"""Stealing and transferring: ``steal_hp`` and ``steal_mp`` take from an enemy for the user, ``transfer_hp`` and ``transfer_mp`` give
the user's own to an ally. Pure code; the dice are scripted (``Scripted``), so every number can be checked by hand.

Roll order of a cast: three speed rolls (the caster's two, the foe's one), then for a spell the saving throw (1 gets through, above
Power does not) or for a skill the to-hit roll, then the amount (0 up to ``added``). A helping effect has no save or hit roll.
"""

import copy

import pytest

from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import specs
from terraforma.fights.combatant import Command
from terraforma.fights.events import EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.replay import apply_events
from terraforma.fights.resolve import do_combat
from terraforma.fights.specs import AbilitySpec, EffectSpec

from .test_fight_rules import RULES, Scripted, fighter, listing, types

SPEEDS = [100, 100, 100]


def play(effect, rolls, caster, foe, kind="skill", cost=0):
    """The caster uses the effect on the foe (who only tries to run, which Pinned refuses). Returns the fight as it was, and what happened."""
    caster.abilities = [AbilitySpec("magic", "Magic", kind, cost, effect)]
    caster.command = Command.SPELL if kind == "spell" else Command.SKILL
    caster.using, caster.target = 0, (1, 0, 0)
    foe.command = Command.RUN
    fight = build_fight({0: {0: [caster]}, 1: {0: [foe]}})
    before = copy.deepcopy(fight)
    return before, fight, do_combat(fight, RULES, Scripted(*SPEEDS, *rolls))


def hurt_at(fighter_, **current):
    fighter_.current.update(current)
    return fighter_


# --- steal_hp -------------------------------------------------------------------------------------------

def test_stealing_life_hurts_the_target_and_heals_the_user_by_what_was_taken():
    caster, foe = hurt_at(fighter("thief", HP=20), HP=10), fighter("mark", HP=50)
    _before, _fight, events = play(EffectSpec(specs.STEAL_HP, 0, base=8), [5, 0], caster, foe)
    assert (foe.current["HP"], caster.current["HP"]) == (42, 18)
    assert ("Damage", 1, 0, 0, 8, False) in listing(events) and ("Restore", 0, 0, 0, "HP", 8) in listing(events)


def test_a_steal_gives_back_only_the_life_the_target_had_and_the_user_has_room_for():
    caster, foe = hurt_at(fighter("thief", HP=20), HP=10), fighter("mark", HP=50)
    foe.current["HP"] = 3
    play(EffectSpec(specs.STEAL_HP, 0, base=8), [5, 0], caster, foe)
    assert (foe.current["HP"], caster.current["HP"]) == (0, 13), "only the 3 that were left"
    caster, foe = hurt_at(fighter("thief", HP=20), HP=18), fighter("mark", HP=50)
    play(EffectSpec(specs.STEAL_HP, 0, base=8), [5, 0], caster, foe)
    assert (foe.current["HP"], caster.current["HP"]) == (42, 20), "the user's maximum is the limit"
    caster = fighter("thief", HP=20)
    _before, _fight, events = play(EffectSpec(specs.STEAL_HP, 0, base=8), [5, 0], caster, fighter("mark", HP=50))
    assert not [each for each in listing(events) if each[0] == "Restore"], "a full user is told of no healing"


def test_stealing_life_can_be_dodged_by_a_skill_and_resisted_by_a_spell():
    caster, foe = hurt_at(fighter("thief", HP=20), HP=10), fighter("mark", HP=50, Dodge=10)
    _before, _fight, events = play(EffectSpec(specs.STEAL_HP, 0, base=8), [70], caster, foe)
    assert types(events)[-1] is EventType.MISS and (foe.current["HP"], caster.current["HP"]) == (50, 10)
    caster, foe = hurt_at(fighter("thief", HP=20, Power=20), HP=10), fighter("mark", HP=50, Resistance=10)
    _before, _fight, events = play(EffectSpec(specs.STEAL_HP, 0, base=8), [30], caster, foe, kind="spell")
    assert types(events)[-1] is EventType.NO_EFFECT and (foe.current["HP"], caster.current["HP"]) == (50, 10)


def test_a_steal_of_a_named_kind_of_damage_with_no_resistance_takes_and_gives_the_whole_amount():
    caster, foe = hurt_at(fighter("thief", HP=20), HP=10), fighter("mark", HP=50)
    play(EffectSpec(specs.STEAL_HP, 0, base=10, attribute="shadow"), [5, 0], caster, foe)
    assert (foe.current["HP"], caster.current["HP"]) == (40, 20), "with no resistance the whole 10 is taken and given"


# --- steal_mp -------------------------------------------------------------------------------------------

def test_stealing_mana_moves_it_from_the_target_to_the_user():
    caster, foe = hurt_at(fighter("thief", MP=10), MP=2), fighter("mark", MP=10)
    _before, _fight, events = play(EffectSpec(specs.STEAL_MP, 0, base=6), [5, 0], caster, foe)
    assert (foe.current["MP"], caster.current["MP"]) == (4, 8)
    assert ("AlterStat", 1, 0, 0, "MP", -6) in listing(events) and ("Restore", 0, 0, 0, "MP", 6) in listing(events)


def test_a_mana_steal_takes_no_more_than_the_target_has_and_the_user_keeps_only_what_fits():
    caster, foe = hurt_at(fighter("thief", MP=10), MP=1), fighter("mark", MP=10)
    foe.current["MP"] = 4
    play(EffectSpec(specs.STEAL_MP, 0, base=9), [5, 0], caster, foe)
    assert (foe.current["MP"], caster.current["MP"]) == (0, 5), "the 4 it had"
    caster, foe = hurt_at(fighter("thief", MP=10), MP=8), fighter("mark", MP=10)
    play(EffectSpec(specs.STEAL_MP, 0, base=6), [5, 0], caster, foe)
    assert (foe.current["MP"], caster.current["MP"]) == (4, 10), "the target loses it all; the user's pool holds 2 of it"


def test_stealing_from_an_empty_pool_does_nothing():
    caster, foe = hurt_at(fighter("thief", MP=10), MP=2), fighter("mark", MP=10)
    foe.current["MP"] = 0
    _before, _fight, events = play(EffectSpec(specs.STEAL_MP, 0, base=6), [5, 0], caster, foe)
    assert types(events)[-1] is EventType.NO_EFFECT and caster.current["MP"] == 2


def test_nothing_is_stolen_from_the_fallen():
    caster, foe = hurt_at(fighter("thief", HP=20), HP=10), fighter("corpse", HP=50)
    foe.current["HP"] = 0
    play(EffectSpec(specs.STEAL_HP, 0, base=8), [], caster, foe)
    assert caster.current["HP"] == 10


# --- transfer_hp and transfer_mp --------------------------------------------------------------------------

def test_transferring_life_gives_the_targets_gain_out_of_the_users_own():
    caster, foe = fighter("giver", HP=20), hurt_at(fighter("friend", HP=50), HP=30)
    _before, _fight, events = play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [0], caster, foe)
    assert (caster.current["HP"], foe.current["HP"]) == (12, 38)
    assert ("Damage", 0, 0, 0, 8, False) in listing(events) and ("Restore", 1, 0, 0, "HP", 8) in listing(events)


def test_a_transfer_never_kills_the_giver_and_never_gives_more_than_fits():
    caster, foe = hurt_at(fighter("giver", HP=20), HP=5), hurt_at(fighter("friend", HP=50), HP=30)
    play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [0], caster, foe)
    assert (caster.current["HP"], foe.current["HP"]) == (1, 34), "4 spare: the giver keeps 1"
    caster, foe = fighter("giver", HP=20), hurt_at(fighter("friend", HP=50), HP=47)
    play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [0], caster, foe)
    assert (caster.current["HP"], foe.current["HP"]) == (17, 50), "only 3 fit, so only 3 leave the giver"


def test_transferring_mana_moves_it_and_can_empty_the_giver():
    caster, foe = hurt_at(fighter("giver", MP=10), MP=6), hurt_at(fighter("friend", MP=10), MP=1)
    _before, _fight, events = play(EffectSpec(specs.TRANSFER_MP, 0, base=9), [0], caster, foe)
    assert (caster.current["MP"], foe.current["MP"]) == (0, 7)
    assert ("AlterStat", 0, 0, 0, "MP", -6) in listing(events) and ("Restore", 1, 0, 0, "MP", 6) in listing(events)


def test_a_transfer_that_moves_nothing_says_so():
    caster, foe = fighter("giver", HP=20), fighter("friend", HP=50)
    _before, _fight, events = play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [0], caster, foe)
    assert types(events)[-1] is EventType.NO_EFFECT and (caster.current["HP"], foe.current["HP"]) == (20, 50), "a full target takes none"
    caster, foe = hurt_at(fighter("giver", HP=20), HP=1), hurt_at(fighter("friend", HP=50), HP=10)
    _before, _fight, events = play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [0], caster, foe)
    assert types(events)[-1] is EventType.NO_EFFECT and caster.current["HP"] == 1, "a giver on 1 has nothing to spare"


def test_a_transfer_to_a_fallen_friend_is_refused_and_a_revive_is_what_that_wants():
    caster, foe = fighter("giver", HP=20), fighter("friend", HP=50)
    foe.current["HP"] = 0
    play(EffectSpec(specs.TRANSFER_HP, 0, base=8), [], caster, foe)
    assert (caster.current["HP"], foe.current["HP"]) == (20, 0)


def test_a_transfer_to_oneself_moves_nothing():
    caster = hurt_at(fighter("giver", HP=20), HP=10)
    foe = fighter("far", HP=50)
    caster.abilities = [AbilitySpec("magic", "Magic", "skill", 0, EffectSpec(specs.TRANSFER_HP, 0, base=8))]
    caster.command, caster.using, caster.target, foe.command = Command.SKILL, 0, (0, 0, 0), Command.RUN
    fight = build_fight({0: {0: [caster]}, 1: {0: [foe]}})
    events = do_combat(fight, RULES, Scripted(*SPEEDS, 0))
    assert caster.current["HP"] == 10 and types(events)[-1] is EventType.NO_EFFECT


# --- the whole thing replays ------------------------------------------------------------------------------

@pytest.mark.parametrize("effect, setup", [
    (EffectSpec(specs.STEAL_HP, 0, base=8), {"giver": {"HP": 10}, "taker": {}}),
    (EffectSpec(specs.STEAL_MP, 0, base=6), {"giver": {"MP": 2}, "taker": {}}),
    (EffectSpec(specs.TRANSFER_HP, 0, base=8), {"giver": {}, "taker": {"HP": 30}}),
    (EffectSpec(specs.TRANSFER_MP, 0, base=6), {"giver": {}, "taker": {"MP": 1}}),
])
def test_replaying_the_events_gives_the_same_fight(effect, setup):
    caster, foe = hurt_at(fighter("a", HP=20, MP=10), **setup["giver"]), hurt_at(fighter("b", HP=50, MP=10), **setup["taker"])
    rolls = [5, 0] if effect.effect in (specs.STEAL_HP, specs.STEAL_MP) else [0]
    before, fight, events = play(effect, rolls, caster, foe)
    apply_events(before, RULES, events)
    assert [before.get(address).current for address in ((0, 0, 0), (1, 0, 0))] == [caster.current, foe.current]


# --- who they aim at, and the seed ---------------------------------------------------------------------------

def test_stealing_is_aimed_at_enemies_and_transferring_at_allies():
    assert [EffectSpec(name).aim for name in (specs.STEAL_HP, specs.STEAL_MP)] == ["enemy", "enemy"]
    assert [EffectSpec(name).aim for name in (specs.TRANSFER_HP, specs.TRANSFER_MP)] == ["ally", "ally"]
    assert all(EffectSpec(name).only_living for name in (specs.STEAL_HP, specs.STEAL_MP, specs.TRANSFER_HP, specs.TRANSFER_MP))
    assert EffectSpec(specs.STEAL_HP).detrimental and not EffectSpec(specs.TRANSFER_HP).detrimental


@pytest.mark.parametrize("name", ["steal_hp", "steal_mp", "transfer_hp", "transfer_mp"])
def test_the_seed_accepts_the_new_effects(name):
    checked = check_seed({"abilities": [{"key": "a", "name": "A", "kind": "spell", "mp_cost": 2, "effect": {"effect": name, "base": 4, "added": 2}}]})
    assert checked["abilities"][0].effect.effect == name
    with pytest.raises(ContentError):
        check_seed({"abilities": [{"key": "a", "name": "A", "kind": "spell", "effect": {"effect": name + "s"}}]})
