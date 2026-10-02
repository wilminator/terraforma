"""Monster AI: DragonStar's way of choosing a command, as pure functions on a snapshot of the fight."""

import pytest

from terraforma.fights.ai import (
    Action,
    AiFighter,
    AiParty,
    Aim,
    Choice,
    Command,
    Committed,
    Goal,
    Slot,
    Usable,
    choose_command,
    combine_on_reach,
    cut,
    goal_for,
    perceived,
    profile_for_level,
    specialty,
)
from terraforma.world.rng import WorldRng

STATS = {"Strength": 100, "Accuracy": 100, "Dodge": 0, "Block": 0, "Power": 50, "Resistance": 0}


def rng(name="ai"):
    return WorldRng(1).stream("fight", name)


def fighter(address, *, hp=100, max_hp=100, mp=10, pxp=10, stats=None, **fields):
    fields.setdefault("hands_share_weapon", True)  # one attack option: left hand
    return AiFighter(address, hp, max_hp, mp, pxp, {**STATS, **(stats or {})}, **fields)


def parties(*groups_by_party, allies=None):
    """Parties 0, 1, ...: every party its own team, an enemy of the others, unless ``allies`` says otherwise."""
    allies = allies or {}
    indexes = range(len(groups_by_party))
    return [
        AiParty(
            index,
            groups,
            allies=frozenset({index, *allies.get(index, ())}),
            enemies=frozenset(set(indexes) - {index} - set(allies.get(index, ()))),
        )
        for index, groups in enumerate(groups_by_party)
    ]


HEAL = Usable("heal", base=30, added=10, mp_cost=5, spell=True)
FIREBALL = Usable("hurt", base=20, added=10, mp_cost=5, spell=True)
SLASH = Usable("hurt", base=20, added=10, mp_cost=0)
HASTE = Usable("increase_stats", mp_cost=0)


# --- profiles ---------------------------------------------------------------------------


def test_specialty_reads_what_the_abilities_are_good_for():
    assert specialty([]) == Action.FIGHTER
    assert specialty([Usable("none")]) == Action.FIGHTER  # not usable in combat
    assert specialty([HEAL, HEAL, SLASH]) == Action.HEALER  # 2 of 3 heal
    assert specialty([HEAL, SLASH, SLASH, SLASH, SLASH]) == Action.PUMMELER  # 20% is not enough
    assert specialty([FIREBALL, FIREBALL, SLASH]) == Action.MAGE
    assert specialty([Usable("increase_stats", spell=True)] * 2 + [FIREBALL, SLASH]) == Action.CASTER
    assert specialty([Usable("increase_stats"), Usable("cause_good_status")]) == Action.FIGHTER


def test_goal_follows_the_action():
    assert goal_for(Action.HEALER) == Goal.PROTECTOR
    assert goal_for(Action.CASTER) == Goal.SCHEMER
    assert goal_for(Action.MAGE) == Goal.DESTRUCTOR
    assert goal_for(Action.FIGHTER) == Goal.DESTRUCTOR


@pytest.mark.parametrize(
    ("level", "action", "goal", "aim", "experience"),
    [
        (1, Action.STUPID, Goal.RANDOM, Aim.STUPID, 50),
        (4, Action.STUPID, Goal.RANDOM, Aim.STUPID, 50),
        (5, Action.NORMAL, Goal.RANDOM, Aim.NORMAL, 40),
        (10, Action.HEALER, Goal.PROTECTOR, Aim.SMART, 30),
        (20, Action.SHARP, Goal.PROTECTOR, Aim.WISE, 20),
        (30, Action.SHARP, Goal.PROTECTOR, Aim.WISE, 10),
        (40, Action.SMART, Goal.PROTECTOR, Aim.WISE, 5),
        (50, Action.OMNIPOTENT, Goal.PROTECTOR, Aim.OMNIPOTENT, 0),
    ],
)
def test_profile_by_level(level, action, goal, aim, experience):
    profile = profile_for_level(level, [HEAL, HEAL], player=False, rng=rng(), jitter=0)
    assert (profile.action, profile.goal, profile.target, profile.experience) == (action, goal, aim, experience)


def test_a_players_hero_stops_at_sharp_wise_and_general():
    profile = profile_for_level(80, [HEAL], player=True, rng=rng(), jitter=0)
    assert (profile.action, profile.target, profile.experience) == (Action.SHARP, Aim.WISE, 5)
    low = profile_for_level(1, [], player=True, rng=rng(), jitter=0)
    assert (low.action, low.target, low.experience) == (Action.STUPID, Aim.STUPID, 50)


def test_level_jitter_is_drawn_from_the_stream_and_repeats():
    first = [profile_for_level(10, [], player=False, rng=rng("a")) for _ in range(3)]
    assert first == [profile_for_level(10, [], player=False, rng=rng("a")) for _ in range(3)]
    levels = {profile_for_level(10, [], player=False, rng=rng(n)).action for n in range(40)}
    assert levels == {Action.NORMAL, Action.FIGHTER}  # jitter -3..3 around level 10 spans 7..13


# --- lists and targets ------------------------------------------------------------------


def test_combine_on_reach():
    values = {0: {0: {0: 1, 1: 2}, 1: {0: 4}}, 1: {0: {0: 8}}}
    assert combine_on_reach(4, 0, "individual", values) == {
        (4, 0, 0, 0, 0): 1, (4, 0, 0, 0, 1): 2, (4, 0, 0, 1, 0): 4, (4, 0, 1, 0, 0): 8,
    }  # fmt: skip
    assert combine_on_reach(4, 0, "group", values) == {(4, 0, 0, 0, 0): 3, (4, 0, 0, 1, 0): 4, (4, 0, 1, 0, 0): 8}
    assert combine_on_reach(4, 0, "party", values) == {(4, 0, 0, 0, 0): 7, (4, 0, 1, 0, 0): 8}
    for reach in ("all_parties", "all_enemies", "all_allies"):
        assert combine_on_reach(5, 2, reach, values) == {(5, 2, 0, 0, 0): 15}


def test_cut_keeps_a_share_and_at_least_one():
    ranked = {(0, 0, 0, 0, i): i for i in range(10)}
    assert len(cut(ranked, 50)) == 5
    assert len(cut(ranked, 25)) == 3  # rounded up
    assert len(cut(ranked, 1)) == 1
    assert len(cut({(0, 0, 0, 0, 0): 1}, 10)) == 1
    assert list(cut(ranked, 10)) == [(0, 0, 0, 0, 0)]  # the first of the list


def test_perceived_stat_is_exact_without_experience_and_skewed_with_it():
    me = fighter((0, 0, 0))
    target = fighter((1, 0, 0), stats={"Block": 80})
    assert perceived(me, target, "Block", rng()) == 80
    sloppy = fighter((0, 0, 0), experience=50)
    seen = {round(perceived(sloppy, target, "Block", rng(n))) for n in range(30)}
    assert 40 <= min(seen) and max(seen) <= 120 and len(seen) > 5  # skewed, within 50%


# --- choosing ---------------------------------------------------------------------------


def test_destructor_attacks_the_enemy_it_hurts_most():
    me = fighter((0, 0, 0), action=Action.NORMAL, goal=Goal.DESTRUCTOR)
    enemies = [
        fighter((1, 0, 0), stats={"Block": 10}),
        fighter((1, 0, 1), stats={"Block": 50}),
        fighter((1, 0, 2), stats={"Block": 0}),
    ]
    choice = choose_command(parties([[me]], [enemies]), (0, 0, 0), rng())
    assert choice == Choice(Command.ATTACK_LEFT, 0, (1, 0, 2))


def test_the_dead_are_not_attacked():
    me = fighter((0, 0, 0), action=Action.NORMAL, goal=Goal.DESTRUCTOR)
    enemies = [fighter((1, 0, 0), hp=0), fighter((1, 0, 1), stats={"Block": 90})]
    assert choose_command(parties([[me]], [enemies]), (0, 0, 0), rng()).target == (1, 0, 1)


def test_a_healer_heals_the_weakest_ally():
    healer = fighter((0, 0, 0), action=Action.HEALER, goal=Goal.PROTECTOR, abilities=[HEAL])
    hurt = fighter((0, 0, 1), hp=20, pxp=10)
    fine = fighter((0, 0, 2), hp=90)
    enemy = fighter((1, 0, 0))
    choice = choose_command(parties([[healer, hurt, fine]], [[enemy]]), (0, 0, 0), rng())
    assert choice == Choice(Command.SPELL, 0, (0, 0, 1))


def test_a_healer_with_no_one_to_heal_fights_instead():
    healer = fighter((0, 0, 0), action=Action.HEALER, goal=Goal.DESTRUCTOR, abilities=[HEAL])
    ally = fighter((0, 0, 1))
    enemy = fighter((1, 0, 0))
    for n in range(10):
        choice = choose_command(parties([[healer, ally]], [[enemy]]), (0, 0, 0), rng(n))
        assert choice.command in (Command.ATTACK_LEFT, Command.SPELL)
        if choice.command == Command.SPELL:  # the heal spell is a normal option, aimed at an ally
            assert choice.target[0] == 0


def test_a_healer_without_healing_plays_normal():
    healer = fighter((0, 0, 0), action=Action.HEALER, goal=Goal.DESTRUCTOR, hp=10)
    enemy = fighter((1, 0, 0))
    assert choose_command(parties([[healer]], [[enemy]]), (0, 0, 0), rng()) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 0))


def test_normal_heals_a_dying_ally_when_it_can():
    me = fighter((0, 0, 0), action=Action.NORMAL, goal=Goal.PROTECTOR, abilities=[HEAL])
    dying = fighter((0, 0, 1), hp=5)
    enemy = fighter((1, 0, 0))
    choice = choose_command(parties([[me, dying]], [[enemy]]), (0, 0, 0), rng())
    assert choice == Choice(Command.SPELL, 0, (0, 0, 1))


def test_a_pummeler_prefers_hurting_skills_over_attacks():
    me = fighter((0, 0, 0), action=Action.PUMMELER, goal=Goal.DESTRUCTOR, abilities=[HASTE, SLASH])
    enemy = fighter((1, 0, 0))
    choice = choose_command(parties([[me]], [[enemy]]), (0, 0, 0), rng())
    assert choice == Choice(Command.SKILL, 1, (1, 0, 0))


def test_a_caster_uses_spells_then_skills_then_items_then_attacks():
    enemy = fighter((1, 0, 0))
    spell = fighter((0, 0, 0), action=Action.CASTER, goal=Goal.SCHEMER, abilities=[SLASH, FIREBALL])
    assert choose_command(parties([[spell]], [[enemy]]), (0, 0, 0), rng()).command == Command.SPELL
    skill = fighter((0, 0, 0), action=Action.CASTER, goal=Goal.SCHEMER, abilities=[SLASH])
    assert choose_command(parties([[skill]], [[enemy]]), (0, 0, 0), rng()).command == Command.SKILL
    out_of_mp = fighter((0, 0, 0), mp=0, action=Action.CASTER, goal=Goal.SCHEMER, abilities=[FIREBALL])
    item = Slot(Usable("hurt", base=10), qty=2)
    with_item = fighter((0, 0, 0), mp=0, action=Action.CASTER, goal=Goal.SCHEMER, abilities=[FIREBALL], inventory=[item])
    assert choose_command(parties([[with_item]], [[enemy]]), (0, 0, 0), rng()).command == Command.ITEM
    assert choose_command(parties([[out_of_mp]], [[enemy]]), (0, 0, 0), rng()).command == Command.ATTACK_LEFT
    empty_item = fighter((0, 0, 0), mp=0, action=Action.CASTER, abilities=[FIREBALL], inventory=[Slot(item.item, qty=0)])
    assert choose_command(parties([[empty_item]], [[enemy]]), (0, 0, 0), rng()).command == Command.ATTACK_LEFT


def test_a_group_follows_its_first_living_member():
    leader = fighter((0, 0, 0), committed=Committed(Command.ATTACK_LEFT, 0, (1, 0, 1)))
    follower = fighter((0, 0, 1), action=Action.NORMAL, goal=Goal.DESTRUCTOR, target=Aim.GROUP)
    enemies = [fighter((1, 0, 0)), fighter((1, 0, 1), stats={"Block": 90})]
    choice = choose_command(parties([[leader, follower]], [enemies]), (0, 0, 1), rng())
    assert choice == Choice(Command.ATTACK_LEFT, 0, (1, 0, 1))
    # With the leader down it chooses for itself: the enemy it hurts most.
    down = fighter((0, 0, 0), hp=0, committed=leader.committed)
    choice = choose_command(parties([[down, follower]], [enemies]), (0, 0, 1), rng())
    assert choice.target == (1, 0, 0)


def test_a_vulture_goes_for_the_enemy_nearly_dead():
    me = fighter((0, 0, 0), action=Action.NORMAL, goal=Goal.DESTRUCTOR, target=Aim.VULTURE)
    enemies = [fighter((1, 0, 0), hp=5, stats={"Block": 90}), fighter((1, 0, 1), hp=90)]
    assert choose_command(parties([[me]], [enemies]), (0, 0, 0), rng()).target == (1, 0, 0)


def test_a_wise_fighter_picks_among_the_best_tenth():
    me = fighter((0, 0, 0), action=Action.NORMAL, goal=Goal.DESTRUCTOR, target=Aim.WISE)
    enemies = [fighter((1, 0, i), stats={"Block": 2 * i}) for i in range(10)]
    choice = choose_command(parties([[me]], [enemies]), (0, 0, 0), rng())
    assert choice.target == (1, 0, 0)  # block 0 is hurt most; ten fighters at 10% keep one


def test_a_group_wide_spell_is_aimed_at_the_party():
    blast = Usable("hurt", targets="party", base=10, spell=True, mp_cost=1)
    me = fighter((0, 0, 0), action=Action.CASTER, goal=Goal.DESTRUCTOR, abilities=[blast])
    weak = [fighter((1, 0, 0)), fighter((1, 0, 1))]
    strong = [fighter((2, 0, 0), stats={"Block": 99})]
    choice = choose_command(parties([[me]], [weak], [strong]), (0, 0, 0), rng())
    assert choice == Choice(Command.SPELL, 0, (1, 0, 0))


def test_a_stupid_fighter_may_try_what_it_cannot_afford():
    pricey = Usable("hurt", base=10, spell=True, mp_cost=99)
    me = fighter((0, 0, 0), mp=0, action=Action.STUPID, goal=Goal.RANDOM, abilities=[pricey])
    enemy = fighter((1, 0, 0))
    seen = {choose_command(parties([[me]], [[enemy]]), (0, 0, 0), rng(n)).command for n in range(30)}
    assert seen == {Command.ATTACK_LEFT, Command.SPELL}


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("goal", list(Goal))
def test_every_action_and_goal_chooses_something_and_repeats(action, goal):
    me = fighter((0, 0, 0), action=action, goal=goal, target=Aim.NORMAL, experience=20, abilities=[HEAL, FIREBALL, SLASH])
    ally = fighter((0, 0, 1), hp=30)
    enemies = [fighter((1, 0, 0)), fighter((1, 0, 1), stats={"Block": 40})]
    fight = parties([[me, ally]], [enemies])
    first = choose_command(fight, (0, 0, 0), rng("same"))
    assert first is not None
    assert first == choose_command(fight, (0, 0, 0), rng("same"))


def test_an_unknown_action_chooses_nothing():
    me = fighter((0, 0, 0), action=99)
    assert choose_command(parties([[me]], [[fighter((1, 0, 0))]]), (0, 0, 0), rng()) is None
