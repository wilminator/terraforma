"""The end of a fight: experience debts, who earns what, gold and level-ups, as events that replay (pure: no database)."""

from terraforma.fights.combatant import Combatant, Command
from terraforma.fights.events import EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.gear import round_half_up
from terraforma.fights.replay import apply_events
from terraforma.fights.resolve import do_combat
from terraforma.fights.rewards import settle
from terraforma.fights.rules import Rules
from terraforma.fights.state import dehydrate, hydrate

RULES = Rules()


class Scripted:
    """A random stream that returns the rolls it was given, in order."""

    def __init__(self, *rolls):
        self.rolls = list(rolls)

    def randint(self, low, high):
        roll = self.rolls.pop(0)
        assert low <= roll <= high, f"scripted roll {roll} is outside {low}..{high}"
        return roll

    def shuffle(self, items):
        pass


def fighter(name="F", hp=20, **fields):
    base = {stat: 0 for stat in RULES.stats}
    base.update(HP=hp, MP=10, Speed=10, Accuracy=10, Strength=10, Dodge=1, Block=1, Power=10, Resistance=1, Focus=10)
    return Combatant(name, base, dict(base), **fields)


def duel(hero=None, monster=None, teams=True):
    hero = hero or fighter("Hero", charid=1, job_need=5, growth={"Strength": 4.0})
    monster = monster or fighter("Monster", hp=1, gold=10)
    fight = build_fight({0: {0: [hero]}, 1: {0: [monster]}})
    if teams:
        fight.parties[0].teams = {7: [1]}
    return fight, hero, monster


def kill(fight, hero, rolls=(100, 10, *[25] * 200)):
    """The hero attacks and the monster defends: a speed roll, a hit roll of 10, then top growth rolls for any level-ups."""
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)
    fight.get((1, 0, 0)).command = Command.DEFEND
    return do_combat(fight, RULES, Scripted(*rolls))


def kinds(events):
    return [each.type for each in events]


def test_a_hit_makes_the_target_owe_the_attacker():
    fight, hero, monster = duel(monster=fighter("Monster", hp=20))
    events = kill(fight, hero)
    debts = [each for each in events if each.type is EventType.XP_DEBT]
    assert len(debts) == 1
    ratio = debts[0].data[6]
    assert debts[0].data[:6] == (1, 0, 0, 0, 0, 0) and 0 < ratio < 1  # the monster owes the hero for the share of its life
    assert debts[0].data[7] == RULES.pxp(monster)
    assert monster.xp_debts == [[0, 0, 0, ratio, RULES.pxp(monster)]]
    assert not [each for each in events if each.type is EventType.XP_EARNED]  # the fight goes on: nothing paid yet


def test_healing_is_owed_too_as_a_negative_share():
    fight, _, monster = duel()
    monster.current["HP"] = 10
    events = RULES.gauge_moved(fight, (1, 0, 0), (1, 0, 0), "HP", 10, 15, 20)
    assert events[0].data[6] == -0.25
    assert RULES.gauge_moved(fight, (1, 0, 0), (1, 0, 0), "HP", 10, 10, 20) == []  # nothing moved
    assert RULES.gauge_moved(fight, (1, 0, 0), (1, 0, 0), "Speed", 10, 12, 20) == []  # not a gauge


def test_killing_a_monster_pays_the_hero_and_the_team_gets_the_gold():
    fight, hero, monster = duel()
    events = kill(fight, hero)
    pxp = RULES.pxp(monster)
    earned = round_half_up(pxp * 0.5) + round_half_up(pxp * 0.2) + round_half_up(pxp * 0.3)
    assert [each.data for each in events if each.type is EventType.XP_EARNED] == [(0, 0, 0, earned)]
    assert [each.data for each in events if each.type is EventType.GOLD] == [(0, 7, 10)]
    assert hero.exp == earned
    assert kinds(events)[-1] in (EventType.XP_EARNED, EventType.GOLD, EventType.LEVEL_UP)


def test_the_hero_levels_up_on_what_it_earned():
    fight, hero, monster = duel()
    events = kill(fight, hero)
    levels = [each for each in events if each.type is EventType.LEVEL_UP]
    expected = 1
    while hero.exp >= RULES.experience_needed(expected, 5):
        expected += 1
    assert hero.level == expected > 1
    assert len(levels) == expected - 1
    assert hero.base["Strength"] == 10 + sum(each.data[4].get("Strength", 0) for each in levels)


def test_a_fight_in_progress_pays_nothing():
    hero = fighter("Hero", charid=1)
    fight, _, _ = duel(hero=hero, monster=fighter("Monster", hp=500))
    events = kill(fight, hero)
    assert not {EventType.XP_EARNED, EventType.GOLD, EventType.LEVEL_UP} & set(kinds(events))
    assert not RULES.fight_is_over(fight)


def test_monsters_and_players_without_a_team_earn_nothing():
    fight, hero, _ = duel(teams=False)
    events = kill(fight, hero)
    assert EventType.XP_EARNED not in kinds(events)
    assert EventType.GOLD not in kinds(events)
    assert hero.exp == 0


def test_the_result_replays_exactly():
    fight, hero, _ = duel()
    hero.command, hero.target = Command.ATTACK_LEFT, (1, 0, 0)  # the commands are stored with the round, not replayed
    fight.get((1, 0, 0)).command = Command.DEFEND
    before = hydrate(dehydrate(fight))
    events = kill(fight, hero)
    apply_events(before, RULES, events)
    assert dehydrate(before) == dehydrate(fight)
    assert before.get((0, 0, 0)).level == hero.level > 1


def test_the_fight_is_over_when_one_party_is_left_or_those_left_are_friends():
    fight, _, monster = duel()
    assert not RULES.fight_is_over(fight)
    monster.current["HP"] = 0
    assert RULES.fight_is_over(fight)
    allies, _, _ = duel()
    allies.parties[0].allies, allies.parties[1].allies = {1}, {0}
    assert RULES.fight_is_over(allies)  # both alive, but not against each other


def test_gold_comes_only_from_the_dead_enemies_and_is_split_between_teams():
    fight = build_fight({
        0: {0: [fighter("A", charid=1), fighter("B", charid=2)]},
        1: {0: [fighter("Dead one", hp=0, gold=10), fighter("Dead two", hp=0, gold=5)]},
        2: {0: [fighter("Standing", gold=99)]},
    })
    fight.parties[0].teams = {7: [1], 8: [2]}
    events = settle(RULES, fight, Scripted())
    # Party 2 is an enemy too, but alive: 15 gold, 7 each (rounded down).
    assert [each.data for each in events if each.type is EventType.GOLD] == [(0, 7, 7), (0, 8, 7)]


def test_experience_needed_and_levelling_use_the_streams_rolls():
    assert [RULES.experience_needed(level, 100) for level in (1, 2, 3)] == [100, 350, 800]
    assert RULES.experience_needed(5, 0) == 0
    hero = fighter("Hero", charid=1, job_need=100, growth={"Strength": 4.0}, exp=360)
    fight = build_fight({0: {0: [hero]}})
    events = RULES.advance(fight, (0, 0, 0), Scripted(0, 25))  # 75% of 4 is 3; 100% is 4
    assert [(each.data[3], each.data[4]) for each in events] == [(2, {"Strength": 3}), (3, {"Strength": 4})]
    assert RULES.advance(build_fight({0: {0: [fighter(exp=10**9)]}}), (0, 0, 0), Scripted()) == []  # no job: no levels


def test_the_top_level_is_the_limit():
    hero = fighter("Hero", job_need=1, growth={"Strength": 1.0}, exp=10**12, level=100)
    assert RULES.advance(build_fight({0: {0: [hero]}}), (0, 0, 0), Scripted()) == []


def test_a_finished_fight_pays_out_once_even_if_another_round_is_played():
    fight, hero, _monster = duel()
    first = kill(fight, hero)
    assert EventType.XP_EARNED in kinds(first)
    exp_after = hero.exp
    again = do_combat(fight, RULES, Scripted(*[100] * 5))
    assert again == [], "nothing is left to play"
    assert hero.exp == exp_after
