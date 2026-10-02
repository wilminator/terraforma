"""The experience tree: DragonStar's rules, as pure functions (no database needed)."""

from terraforma.fights.experience import (
    Debt,
    Fighter,
    Party,
    bonus_experience,
    debt_scales,
    experience_needed_after,
    gold_per_team,
    process_experience,
    round_half_up,
)

HERO = (0, 0, 0)
HERO_2 = (0, 0, 1)
MONSTER = (1, 0, 0)


def duel(monster_debts, *, monster_dead=True, hero_debts=()):
    heroes = Party(
        index=0,
        fighters=[Fighter(HERO, hero_debts, charid=1)],
        dead=False,
        allies=frozenset({0}),
        enemies=frozenset({1}),
        teams={1: [1]},
    )
    monsters = Party(
        index=1,
        fighters=[Fighter(MONSTER, monster_debts)],
        dead=monster_dead,
        allies=frozenset({1}),
        enemies=frozenset({0}),
    )
    return [heroes, monsters]


def test_round_half_up_goes_away_from_zero():
    assert [round_half_up(x) for x in (0.4, 0.5, 1.5, 2.5, -2.5, -0.4)] == [0, 1, 2, 3, -3, 0]


def test_killing_a_monster_earns_its_pxp_split_in_three_pools():
    result = process_experience(duel([Debt(HERO, 1.0, 100)]))
    # Credit 100: own half 50, party pool 20% = 20, team pool 30% = 30.
    assert result.credits[0].total == 100
    assert result.earned == {HERO: 100}
    assert result.losers == [1, 0]
    assert result.winners == [0]
    assert result.over


def test_monsters_never_earn_but_still_count():
    parties = duel([Debt(HERO, 1.0, 100)])
    result = process_experience(parties)
    assert MONSTER not in result.earned
    assert result.credits[1].count == 1


def test_a_fighter_never_owes_more_than_one():
    assert debt_scales(duel([Debt(HERO, 0.6, 10), Debt(HERO_2, 0.4, 10)]))[MONSTER] == 1.0
    assert debt_scales(duel([Debt(HERO, 1.0, 10), Debt(HERO_2, 1.0, 10)]))[MONSTER] == 0.5


def test_over_owed_debts_are_scaled_down_and_shared_by_what_each_did():
    heroes = Party(
        index=0,
        fighters=[Fighter(HERO, charid=1), Fighter(HERO_2, charid=2)],
        dead=False,
        allies=frozenset({0}),
        enemies=frozenset({1}),
        teams={1: [1], 2: [2]},
    )
    monsters = Party(
        index=1,
        fighters=[Fighter(MONSTER, [Debt(HERO, 1.0, 100), Debt(HERO_2, 1.0, 100)])],
        dead=True,
        allies=frozenset({1}),
        enemies=frozenset({0}),
    )
    result = process_experience([heroes, monsters])
    assert result.credits[0].per_fighter == {HERO: 50, HERO_2: 50}
    # Own 25 + party pool 10 (20 per 2 members) + team pool 15.
    assert result.earned == {HERO: 50, HERO_2: 50}


def test_no_one_is_paid_while_two_enemy_parties_still_stand():
    result = process_experience(duel([Debt(HERO, 0.5, 100)], monster_dead=False))
    assert result.credits[0].total == 0
    assert result.earned == {HERO: 0}
    assert result.winners == [0, 1]
    assert not result.over


def test_allies_pay_for_healing_but_not_for_harm():
    heroes = Party(
        index=0,
        fighters=[Fighter(HERO, charid=1)],
        dead=False,
        allies=frozenset({0, 1}),
        enemies=frozenset(),
        teams={1: [1]},
    )
    friends = Party(
        index=1,
        fighters=[Fighter(MONSTER, [Debt(HERO, 0.5, 100), Debt(HERO, -0.5, 40)])],
        dead=False,
        allies=frozenset({0, 1}),
        enemies=frozenset(),
    )
    result = process_experience([heroes, friends])
    assert result.over
    assert result.credits[0].total == 20  # only the healing: 0.5 * 40
    assert result.earned == {HERO: 20}  # 10 own + 4 party pool + 6 team pool


def test_debts_to_a_party_that_already_left_are_not_paid():
    # Both parties die: the first to leave is paid, the debt to it afterwards is not.
    heroes = Party(
        index=0,
        fighters=[Fighter(HERO, [Debt(MONSTER, 1.0, 100)], charid=1)],
        dead=True,
        allies=frozenset({0}),
        enemies=frozenset({1}),
        teams={1: [1]},
    )
    monsters = Party(
        index=1,
        fighters=[Fighter(MONSTER, [Debt(HERO, 1.0, 100)])],
        dead=True,
        allies=frozenset({1}),
        enemies=frozenset({0}),
    )
    result = process_experience([heroes, monsters])
    assert result.credits[0].total == 100
    assert result.credits[1].total == 100
    assert result.losers == [0, 1]
    assert result.winners == []
    assert result.over


def test_bonus_for_beating_a_players_monsters():
    assert bonus_experience(100, 0.25) == 25
    assert bonus_experience(99, 0.25) == 24
    assert bonus_experience(-5, 0.25) == 0


def test_gold_is_split_between_teams_rounding_down():
    assert gold_per_team([10, 15, 3], 2) == 14
    assert gold_per_team([10], 0) == 0
    assert gold_per_team([], 3) == 0


def test_experience_needed_for_the_next_level():
    # Level 1: 100/2 * ((4*3/2) - 1) = 250 more.
    assert experience_needed_after(1, 100, 100) == 350
    assert experience_needed_after(2, 100, 350) == 350 + 50 * ((5 * 4 // 2) - 1)
