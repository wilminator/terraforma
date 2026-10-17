"""A blow that leaves a status: an item's ``attack.status`` (seed format, fight resolution, saved state) and what PXP counts for it.

Pure code, so these need no database. A ``Scripted`` stream hands out exactly the rolls a test chooses: a round's first two rolls are
the fighters' Speed rolls, then a hit roll, then (only for a status with less than a sure chance) the roll for the status.
"""

import copy
import json

import pytest

from terraforma.content.schema import ContentError, check_seed
from terraforma.fights import specs
from terraforma.fights.combatant import Command
from terraforma.fights.content import item_spec
from terraforma.fights.events import EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.potential import STATUS_DEFAULT_ROUNDS, STATUS_ROUND_WORTH, attack_status_worth
from terraforma.fights.resolve import do_combat
from terraforma.fights.specs import EffectSpec, ItemSpec
from terraforma.fights.state import dehydrate, hydrate
from terraforma.fights.status import StatusToken

from .test_fight_rules import RULES, Scripted, fighter, listing
from .test_statuses import STATUS, poison

pytestmark = pytest.mark.fights

A, B = (0, 0, 0), (1, 0, 0)


def arrow(**changes) -> ItemSpec:
    return ItemSpec("arrow", "Arrow", ("ammo",), ammo_type="arrow", **changes)


def archer(ammo: ItemSpec, count=5):
    hero = fighter("archer", Strength=20, Accuracy=10)
    hero.inventory = [[ItemSpec("bow", "Bow", ("hand",), ammo_type="arrow"), 1], [ammo, count]]
    hero.equipment = {"lhand": 0, "lammo": 1}
    return hero


def shoot(ammo: ItemSpec, rolls, foe_hp=100):
    """The archer shoots the foe once; the foe has the poison status known to the fight. Returns (events, fight, foe)."""
    hero, foe = archer(ammo), fighter("foe", HP=foe_hp, Dodge=1)
    fight = build_fight({0: {0: [hero]}, 1: {0: [foe]}}, {"poison": poison()})
    hero.command, hero.target = Command.ATTACK_LEFT, B
    foe.command = Command.RUN
    return do_combat(fight, RULES, Scripted(*rolls)), fight, fight.get(B)


# --- the fight ----------------------------------------------------------------------------------------------------------------

def test_a_sure_status_is_left_by_a_blow_that_lands_and_draws_no_roll_of_its_own():
    events, _fight, foe = shoot(arrow(attack_status="poison", attack_status_duration=2), (100, 100, 2))
    assert ("StatusApplied", 1, 0, 0, "poison", 0, 0, 0, 2) in listing(events)
    assert [(token.spec.key, token.source, token.duration) for token in foe.tokens] == [("poison", A, 2)]


def test_a_status_without_a_duration_lasts_as_long_as_the_status_does():
    _events, _fight, foe = shoot(arrow(attack_status="poison"), (100, 100, 2))
    assert foe.tokens[0].duration == 3


def test_a_missed_blow_leaves_nothing():
    events, _fight, foe = shoot(arrow(attack_status="poison"), (100, 100, 100))  # 100 on a 50 percent chance to hit
    assert EventType.MISS in [each.type for each in events] and foe.tokens == []


def test_a_blow_that_did_not_land_does_not_even_roll_for_its_status():
    # The scripted stream refuses a roll that was not given, so this fails if the status drew one after the miss.
    shoot(arrow(attack_status="poison", attack_status_chance=10), (100, 100, 100))


@pytest.mark.parametrize("roll, left", [(10, True), (11, False)])
def test_a_status_with_a_chance_is_left_on_a_roll_within_it(roll, left):
    _events, _fight, foe = shoot(arrow(attack_status="poison", attack_status_chance=10), (100, 100, 2, roll))
    assert bool(foe.tokens) is left


def test_a_blow_that_fells_its_target_leaves_no_status():
    _events, _fight, foe = shoot(arrow(attack_status="poison"), (100, 100, 2), foe_hp=1)
    assert foe.current["HP"] == 0 and foe.tokens == []


def test_a_weapon_that_needs_no_ammunition_can_carry_the_status_itself():
    hero, foe = fighter("hero", Strength=20, Accuracy=10), fighter("foe", HP=100, Dodge=1)
    hero.inventory, hero.equipment = [[ItemSpec("dagger", "Dagger", ("hand",), attack_status="poison"), 1]], {"lhand": 0}
    fight = build_fight({0: {0: [hero]}, 1: {0: [foe]}}, {"poison": poison()})
    hero.command, hero.target, foe.command = Command.ATTACK_LEFT, B, Command.RUN
    do_combat(fight, RULES, Scripted(100, 100, 2))
    assert [token.spec.key for token in foe.tokens] == ["poison"]


def test_loaded_ammunition_decides_the_status_not_the_bow():
    plain = EffectSpec(specs.HURT, 0, 10, 10, "none")
    assert archer(arrow()).weapon_effect(RULES, Command.ATTACK_LEFT) == plain, "an arrow with no status leaves none"
    poisoned = archer(arrow(attack_status="poison", attack_status_duration=2, attack_status_chance=40))
    assert poisoned.weapon_effect(RULES, Command.ATTACK_LEFT) == EffectSpec(specs.HURT, 0, 10, 10, "none", "poison", 2, False, 40)


def test_a_hero_blow_with_no_status_keeps_its_dice():
    # No roll for the status is drawn when there is none: the same rolls give the same fight as before this feature.
    events, _fight, foe = shoot(arrow(), (100, 100, 2))
    assert foe.tokens == [] and not any(each[0] == "StatusApplied" for each in listing(events))


# --- the saved fight ---------------------------------------------------------------------------------------------------------

def test_the_snapshot_keeps_a_blow_s_status_through_json_and_leaves_a_plain_item_as_it_was():
    hero = archer(arrow(attack_status="poison", attack_status_duration=2, attack_status_chance=40))
    fight = build_fight({0: {0: [hero]}, 1: {0: [fighter("foe")]}}, {"poison": poison()})
    raw = json.loads(json.dumps(dehydrate(fight)))
    again = hydrate(raw)
    assert dehydrate(again) == dehydrate(fight)
    kept = again.get(A).inventory[1][0]
    assert (kept.attack_status, kept.attack_status_duration, kept.attack_status_chance) == ("poison", 2, 40)
    stored = json.dumps(dehydrate(build_fight({0: {0: [archer(arrow())]}, 1: {0: [fighter("foe")]}}, {})))
    assert "attack_status" not in stored and "status_chance" not in stored, "a fight stored without one reads back exactly as written"


# --- the seed ------------------------------------------------------------------------------------------------------------------

def arrows(**rider):
    return {"items": [{"key": "toxic_arrow", "name": "Toxic arrow", "equip_slots": ["ammo"], "attack": {"ammo_type": "arrow", "status": rider}}],
            "statuses": [STATUS]}


def refused(data, text):
    with pytest.raises(ContentError) as caught:
        check_seed(data)
    assert text in str(caught.value), str(caught.value)


def test_an_item_s_attack_may_name_a_bad_status_with_a_duration_and_a_chance():
    plain = check_seed(arrows(status="poison"))["items"][0].attack.status
    assert (plain.status, plain.duration, plain.chance) == ("poison", None, 100)
    rider = check_seed(arrows(status="poison", duration=2, chance=25))["items"][0]
    spec = item_spec(type("Row", (), {**rider.model_dump(), "attack": rider.attack.model_dump()}))
    assert (spec.attack_status, spec.attack_status_duration, spec.attack_status_chance) == ("poison", 2, 25)


def test_an_attack_without_a_status_is_as_it_was():
    data = copy.deepcopy(arrows(status="poison"))
    del data["items"][0]["attack"]["status"]
    assert check_seed(data)["items"][0].attack.status is None
    spec = item_spec(type("Row", (), {**check_seed(data)["items"][0].model_dump(), "attack": check_seed(data)["items"][0].attack.model_dump()}))
    assert (spec.attack_status, spec.attack_status_duration, spec.attack_status_chance) == ("", None, 100)


def test_an_attack_s_status_is_checked_against_statuses_json():
    refused(arrows(status="nope"), "the attack's status names 'nope', which statuses.json doesn't have")
    good = {**arrows(status="poison"), "statuses": [{**STATUS, "kind": "good"}]}
    refused(good, "which is a good status (a blow places bad ones)")
    refused({"items": arrows(status="poison")["items"]}, "statuses.json doesn't have")


@pytest.mark.parametrize("rider, text", [
    ({"status": "poison", "chance": 0}, "chance"),
    ({"status": "poison", "chance": 101}, "chance"),
    ({"status": "poison", "duration": 0}, "duration"),
    ({"status": "poison", "power": 3}, "Extra inputs are not permitted"),
    ({}, "status"),
])
def test_a_rider_that_makes_no_sense_is_refused(rider, text):
    refused(arrows(**rider), text)


# --- PXP -----------------------------------------------------------------------------------------------------------------------

def test_a_status_adds_its_rounds_worth_by_the_chance_to_the_blow():
    hurt = EffectSpec(specs.HURT, 0, 10, 10, "none", "poison", 4, False, 50)
    assert attack_status_worth(hurt) == STATUS_ROUND_WORTH * 4 * 0.5
    assert attack_status_worth(EffectSpec(specs.HURT, 0, 10, 10, "none", "poison")) == STATUS_ROUND_WORTH * STATUS_DEFAULT_ROUNDS
    assert attack_status_worth(EffectSpec(specs.HURT, 0, 10, 10)) == 0


def test_ammunition_that_places_a_status_raises_pxp_and_a_longer_surer_one_raises_it_more():
    ratings = [RULES.pxp(archer(arrow(**riders))) for riders in (
        {}, {"attack_status": "poison", "attack_status_duration": 1, "attack_status_chance": 20},
        {"attack_status": "poison", "attack_status_duration": 1}, {"attack_status": "poison", "attack_status_duration": 5},
    )]
    assert ratings == sorted(set(ratings)), ratings


def test_a_weapon_s_own_status_counts_too_and_being_poisoned_still_changes_nothing():
    hero = fighter("hero", Strength=20)
    hero.inventory, hero.equipment = [[ItemSpec("dagger", "Dagger", ("hand",)), 1]], {"lhand": 0}
    plain = RULES.pxp(hero)
    hero.inventory = [[ItemSpec("dagger", "Dagger", ("hand",), attack_status="poison"), 1]]
    assert RULES.pxp(hero) > plain
    afflicted = archer(arrow())
    before = RULES.pxp(afflicted)
    afflicted.tokens.append(StatusToken(poison(), A, 3, 0))
    assert RULES.pxp(afflicted) == before
