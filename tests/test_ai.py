"""Monster AI: DragonStar's way of choosing a command, on the engine's own fights (pure: no database)."""

import pytest

from terraforma.fights.ai import (
    Action,
    Aim,
    Choice,
    Goal,
    choose_command,
    combine_on_reach,
    commit,
    cut,
    goal_for,
    perceived,
    profile_for_level,
    specialty,
)
from terraforma.fights.combatant import Combatant, Command
from terraforma.fights.fight import build_fight
from terraforma.fights.potential import half_life, potential, target_rating
from terraforma.fights.rules import Rules
from terraforma.fights.specs import (
    ALL_ENEMIES,
    GROUP,
    PARTY,
    AbilitySpec,
    EffectSpec,
    ItemSpec,
)
from terraforma.world.rng import WorldRng

RULES = Rules()
STATS = {
    "HP": 100, "MP": 10, "Speed": 8, "Accuracy": 100, "Strength": 100, "Dodge": 1, "Block": 0,
    "Power": 50, "Resistance": 0, "Focus": 1,
}  # fmt: skip
TWO_HANDED = ItemSpec(key="staff", name="Staff", equip_slots=("lhand", "rhand"))


def rng(name="ai"):
    return WorldRng(1).stream("fight", name)


def fighter(*, hp=100, mp=10, block=0, dodge=1, **fields):
    """A combatant holding a two-handed staff, so it has one attack option: the left hand."""
    base = {**STATS, "Block": block, "Dodge": dodge}
    fields.setdefault("inventory", [[TWO_HANDED, 1]])
    fields.setdefault("equipment", {"lhand": 0, "rhand": 0})
    return Combatant("F", dict(base), {**base, "HP": hp, "MP": mp}, **fields)


def spell(effect, *, base=0, added=0, targets=0, cost=5, kind="spell"):
    return AbilitySpec("a", "A", kind, cost, EffectSpec(effect, targets, base, added))


HEAL = spell("heal", base=30, added=10)
FIREBALL = spell("hurt", base=20, added=10)
SLASH = spell("hurt", base=20, added=10, cost=0, kind="skill")
HASTE = spell("cause_good_status", cost=0, kind="skill")


def fight_of(*parties, **alignment):
    """Fighters per party (a list of groups, each a list), numbered as the engine does."""
    fight = build_fight({index: dict(enumerate(groups)) for index, groups in enumerate(parties)})
    for index, (allies, enemies) in alignment.items():
        fight.parties[int(index.removeprefix("p"))].allies, fight.parties[int(index.removeprefix("p"))].enemies = allies, enemies
    return fight


def play(fight, address, name="ai"):
    return choose_command(RULES, fight, address, rng(name))


# --- profiles ---------------------------------------------------------------------------


def test_specialty_reads_what_the_abilities_are_good_for():
    assert specialty([]) == Action.FIGHTER
    assert specialty([spell("none")]) == Action.FIGHTER  # not usable in combat
    assert specialty([HEAL, HEAL, SLASH]) == Action.HEALER  # 2 of 3 heal
    assert specialty([HEAL, SLASH, SLASH, SLASH, SLASH]) == Action.PUMMELER  # 20% is not enough
    assert specialty([FIREBALL, FIREBALL, SLASH]) == Action.MAGE
    assert specialty([spell("cause_good_status")] * 2 + [FIREBALL, SLASH]) == Action.CASTER
    assert specialty([HASTE, spell("cause_good_status", kind="skill")]) == Action.FIGHTER


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


def test_a_profile_sets_the_fighters_numbers():
    me = fighter()
    profile_for_level(20, [HEAL], player=False, rng=rng(), jitter=0).apply(me)
    assert (me.ai_action, me.ai_goal, me.ai_target, me.ai_experience) == (Action.SHARP, Goal.PROTECTOR, Aim.WISE, 20)


def test_level_jitter_is_drawn_from_the_stream_and_repeats():
    first = [profile_for_level(10, [], player=False, rng=rng("a")) for _ in range(3)]
    assert first == [profile_for_level(10, [], player=False, rng=rng("a")) for _ in range(3)]
    levels = {profile_for_level(10, [], player=False, rng=rng(n)).action for n in range(40)}
    assert levels == {Action.NORMAL, Action.FIGHTER}  # jitter -3..3 around level 10 spans 7..13


# --- lists and targets ------------------------------------------------------------------


def test_combine_on_reach():
    values = {0: {0: {0: 1, 1: 2}, 1: {0: 4}}, 1: {0: {0: 8}}}
    assert combine_on_reach(4, 0, 0, values) == {
        (4, 0, 0, 0, 0): 1, (4, 0, 0, 0, 1): 2, (4, 0, 0, 1, 0): 4, (4, 0, 1, 0, 0): 8,
    }  # fmt: skip
    assert combine_on_reach(4, 0, 1, values)[(4, 0, 0, 0, 0)] == 3  # one neighbour either side
    assert combine_on_reach(4, 0, GROUP, values) == {(4, 0, 0, 0, 0): 3, (4, 0, 0, 1, 0): 4, (4, 0, 1, 0, 0): 8}
    assert combine_on_reach(4, 0, PARTY, values) == {(4, 0, 0, 0, 0): 7, (4, 0, 1, 0, 0): 8}
    for reach in (-3, -4, -5, -6, -7):
        assert combine_on_reach(5, 2, reach, values) == {(5, 2, 0, 0, 0): 15}


def test_cut_keeps_a_share_and_at_least_one():
    ranked = {(0, 0, 0, 0, i): i for i in range(10)}
    assert len(cut(ranked, 50)) == 5
    assert len(cut(ranked, 25)) == 3  # rounded up
    assert len(cut(ranked, 1)) == 1
    assert len(cut({(0, 0, 0, 0, 0): 1}, 10)) == 1
    assert list(cut(ranked, 10)) == [(0, 0, 0, 0, 0)]  # the first of the list


def test_perceived_stat_is_exact_without_experience_and_skewed_with_it():
    me, target = fighter(), fighter(block=80)
    assert perceived(me, target, RULES, "Block", rng()) == 80
    me.ai_experience = 50
    seen = {round(perceived(me, target, RULES, "Block", rng(n))) for n in range(30)}
    assert 40 <= min(seen) and max(seen) <= 120 and len(seen) > 5  # skewed, within 50%


# --- choosing ---------------------------------------------------------------------------


def test_destructor_attacks_the_enemy_it_hurts_most():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR)
    enemies = [fighter(block=10), fighter(block=50), fighter(block=0)]
    fight = fight_of([[me]], [enemies])
    assert play(fight, (0, 0, 0)) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 2))


def test_the_dead_are_not_attacked():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR)
    fight = fight_of([[me]], [[fighter(hp=0), fighter(block=90)]])
    assert play(fight, (0, 0, 0)).target == (1, 0, 1)


def test_a_healer_heals_the_weakest_ally():
    healer = fighter(ai_action=Action.HEALER, ai_goal=Goal.PROTECTOR, abilities=[HEAL])
    fight = fight_of([[healer, fighter(hp=20), fighter(hp=90)]], [[fighter()]])
    assert play(fight, (0, 0, 0)) == Choice(Command.SPELL, 0, (0, 0, 1))


def test_a_healer_with_no_one_to_heal_fights_instead():
    healer = fighter(ai_action=Action.HEALER, ai_goal=Goal.DESTRUCTOR, abilities=[HEAL])
    fight = fight_of([[healer, fighter()]], [[fighter()]])
    for n in range(10):
        choice = play(fight, (0, 0, 0), n)
        assert choice.command in (Command.ATTACK_LEFT, Command.SPELL)


def test_a_healer_without_healing_plays_normal():
    healer = fighter(ai_action=Action.HEALER, ai_goal=Goal.DESTRUCTOR, hp=10)
    fight = fight_of([[healer]], [[fighter()]])
    assert play(fight, (0, 0, 0)) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 0))


def test_normal_heals_a_dying_ally_when_it_can():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.PROTECTOR, abilities=[HEAL])
    fight = fight_of([[me, fighter(hp=5)]], [[fighter()]])
    assert play(fight, (0, 0, 0)) == Choice(Command.SPELL, 0, (0, 0, 1))


def test_a_pummeler_prefers_hurting_skills_over_attacks():
    me = fighter(ai_action=Action.PUMMELER, ai_goal=Goal.DESTRUCTOR, abilities=[HASTE, SLASH])
    fight = fight_of([[me]], [[fighter()]])
    assert play(fight, (0, 0, 0)) == Choice(Command.SKILL, 1, (1, 0, 0))


def test_a_caster_uses_spells_then_skills_then_items_then_attacks():
    def caster(**fields):
        return fighter(ai_action=Action.CASTER, ai_goal=Goal.SCHEMER, **fields)

    def command(me):
        return play(fight_of([[me]], [[fighter()]]), (0, 0, 0)).command

    bomb = ItemSpec(key="bomb", use_effect=EffectSpec("hurt", 0, 10, 0))
    assert command(caster(abilities=[SLASH, FIREBALL])) == Command.SPELL
    assert command(caster(abilities=[SLASH])) == Command.SKILL
    stock = [[TWO_HANDED, 1], [bomb, 2]]
    assert command(caster(mp=0, abilities=[FIREBALL], inventory=stock, equipment={"lhand": 0, "rhand": 0})) == Command.ITEM
    assert command(caster(mp=0, abilities=[FIREBALL])) == Command.ATTACK_LEFT
    used_up = [[TWO_HANDED, 1], [bomb, 0]]
    assert command(caster(mp=0, abilities=[FIREBALL], inventory=used_up, equipment={"lhand": 0, "rhand": 0})) == Command.ATTACK_LEFT


def test_a_group_follows_its_first_living_member():
    leader = fighter(command=Command.ATTACK_LEFT, target=(1, 0, 1))
    follower = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR, ai_target=Aim.GROUP)
    enemies = [fighter(), fighter(block=90)]
    assert play(fight_of([[leader, follower]], [enemies]), (0, 0, 1)) == Choice(Command.ATTACK_LEFT, 0, (1, 0, 1))
    # With the leader down it chooses for itself: the enemy it hurts most.
    down = fighter(hp=0, command=Command.ATTACK_LEFT, target=(1, 0, 1))
    assert play(fight_of([[down, follower]], [enemies]), (0, 0, 1)).target == (1, 0, 0)


def test_a_vulture_goes_for_the_enemy_nearly_dead():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR, ai_target=Aim.VULTURE)
    fight = fight_of([[me]], [[fighter(hp=5, block=90), fighter(hp=90)]])
    assert play(fight, (0, 0, 0)).target == (1, 0, 0)


def test_a_wise_fighter_picks_among_the_best_tenth():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR, ai_target=Aim.WISE)
    fight = fight_of([[me]], [[fighter(block=2 * i) for i in range(10)]])
    assert play(fight, (0, 0, 0)).target == (1, 0, 0)  # block 0 is hurt most; eleven keys at 10% keep two, the best wins


def test_a_party_wide_spell_is_aimed_at_the_party():
    blast = spell("hurt", base=10, targets=PARTY, cost=1)
    me = fighter(ai_action=Action.CASTER, ai_goal=Goal.DESTRUCTOR, abilities=[blast])
    fight = fight_of([[me]], [[fighter(), fighter()]], [[fighter(block=99)]])
    assert play(fight, (0, 0, 0)) == Choice(Command.SPELL, 0, (1, 0, 0))


def test_an_everyone_spell_has_no_single_target():
    quake = spell("hurt", base=10, targets=ALL_ENEMIES, cost=1)
    me = fighter(ai_action=Action.CASTER, ai_goal=Goal.DESTRUCTOR, abilities=[quake])
    fight = fight_of([[me]], [[fighter(), fighter()]])
    assert play(fight, (0, 0, 0)) == Choice(Command.SPELL, 0, (0, 0, 0))


def test_allies_are_not_attacked_and_neutrals_are_avoided():
    me = fighter(ai_action=Action.NORMAL, ai_goal=Goal.DESTRUCTOR)
    fight = fight_of([[me]], [[fighter(block=0)]], [[fighter(block=99)]], p0=({0, 1}, {2}))
    assert play(fight, (0, 0, 0)).target == (2, 0, 0)  # the enemy, though it blocks more than the ally


def test_a_stupid_fighter_may_try_what_it_cannot_afford():
    pricey = spell("hurt", base=10, cost=99)
    me = fighter(mp=0, ai_action=Action.STUPID, ai_goal=Goal.RANDOM, abilities=[pricey])
    fight = fight_of([[me]], [[fighter()]])
    seen = {play(fight, (0, 0, 0), n).command for n in range(30)}
    assert seen == {Command.ATTACK_LEFT, Command.SPELL}


@pytest.mark.parametrize("action", list(Action))
@pytest.mark.parametrize("goal", list(Goal))
def test_every_action_and_goal_chooses_something_and_repeats(action, goal):
    me = fighter(ai_action=action, ai_goal=goal, ai_target=Aim.NORMAL, ai_experience=20, abilities=[HEAL, FIREBALL, SLASH])
    fight = fight_of([[me, fighter(hp=30)]], [[fighter(), fighter(block=40)]])
    first = play(fight, (0, 0, 0), "same")
    assert first is not None
    assert first == play(fight, (0, 0, 0), "same")


def test_an_unknown_action_chooses_nothing_and_the_fighter_defends():
    me = fighter(ai_action=99, command=Command.ATTACK_LEFT)
    fight = fight_of([[me]], [[fighter()]])
    choice = play(fight, (0, 0, 0))
    assert choice is None
    commit(me, choice)
    assert me.command == Command.DEFEND


def test_commit_gives_the_fighter_its_command():
    me = fighter()
    commit(me, Choice(Command.SPELL, 2, (1, 0, 3)))
    assert (me.command, me.using, me.target) == (Command.SPELL, 2, (1, 0, 3))


# --- potential experience ---------------------------------------------------------------


def bare(**stats):
    base = {"HP": 100, "MP": 10, "Speed": 8, "Accuracy": 8, "Strength": 8, "Dodge": 8, "Block": 8, "Power": 1, "Resistance": 1, "Focus": 1}
    base.update(stats)
    return Combatant("B", dict(base), dict(base))


def test_potential_of_a_bare_handed_fighter():
    # Defence round(cbrt(8*8*100)) = 19; offence in each hand 2*2*2 * half_life(0, None) = 16; magic defence 10.
    assert potential(RULES, bare()) == 19 + 16 + 10
    assert RULES.pxp(bare()) == potential(RULES, bare())


def test_potential_counts_abilities_it_can_pay_for():
    magic = bare()
    magic.abilities = [spell("hurt", base=20, added=10)]  # 5 MP of 10: sqrt(1 * 25) = 5 -> round(5^.25 * 8^.25) = 3
    assert potential(RULES, magic) == 45 + 3
    magic.base["MP"] = 2  # it can no longer pay for it
    assert potential(RULES, magic) == 45


def test_potential_grows_with_stats():
    assert potential(RULES, bare(Strength=64)) > potential(RULES, bare())
    assert potential(RULES, bare(HP=800)) > potential(RULES, bare())


def test_ratings():
    assert [target_rating(n) for n in (0, 1, 2, 3, 5, GROUP, PARTY, -3, -6)] == [1, 1.5, 2, 2.5, 2.5, 3, 9, 27, 27]
    assert half_life(0, None) == 2
    assert half_life(0, 3) == 2 - 0.25
    assert half_life(2, None) == 0.5


@pytest.mark.parametrize("action", [Action.FIGHTER, Action.MAGE, Action.SHARP])
def test_the_better_half_of_the_targets_is_kept_not_the_first_half(action):
    # Half of a two-name list is its first name, which is the fighter's own side: it must still go for the enemy.
    me = fighter(ai_action=action, ai_goal=Goal.DESTRUCTOR)
    fight = fight_of([[me]], [[fighter()]])
    for n in range(10):
        assert play(fight, (0, 0, 0), n).target == (1, 0, 0)
