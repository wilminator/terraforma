"""The balance simulator (terraforma.tools.balance): a few fights of the example game, enough to check the pieces. The tool itself is run
on demand for hundreds of fights; no test does that."""

import json
import random
from pathlib import Path

import pytest

import terraforma.example
from terraforma.tools import balance

pytestmark = pytest.mark.fights

SEED = Path(terraforma.example.__file__).parent / "seed"


@pytest.fixture(scope="module")
def content():
    return balance.load_content(SEED)


def test_encounters_grow_with_the_level():
    assert balance.encounter_sizes(1) == (3, 5)
    assert balance.encounter_sizes(50) == (4, 30)
    assert balance.encounter_sizes(0) == (3, 5)  # below the first level counts as the first
    assert balance.encounter_sizes(80) == (4, 30)  # and past the last as the last
    sizes = [balance.encounter_sizes(level) for level in range(1, 51)]
    assert all(low <= high for low, high in sizes)
    assert sizes == sorted(sizes), "neither bound ever goes down as the level goes up"


def test_heroes_stand_in_groups_of_five_and_monsters_in_groups_of_ten(content):
    heroes = [balance.hero_at_level(content, "fighter", 1, random.Random(1)) for _ in range(7)]
    assert (balance.GROUP_SIZE, balance.FOE_GROUP_SIZE) == (5, 10)
    for count, sizes in [(1, [1]), (5, [5]), (10, [10]), (11, [10, 1]), (25, [10, 10, 5]), (30, [10, 10, 10])]:
        sides = balance.layout(heroes, [balance.foe(content, "slime") for _ in range(count)])
        assert [len(group) for group in sides[balance.FOES].values()] == sizes
        assert [len(group) for group in sides[balance.HEROES].values()] == [5, 2]


def test_a_hero_is_made_as_the_engine_makes_one(content):
    job = content.jobs["fighter"]
    first = balance.hero_at_level(content, "fighter", 1, random.Random(1))
    assert first.base == {stat: round(job.stat_growth.get(stat, 0)) for stat in content.rules.stats}
    assert first.current == {"HP": first.base["HP"], "MP": first.base["MP"]}
    assert [ability.key for ability in first.abilities] == []  # the fighter's first ability comes at level 2
    tenth = balance.hero_at_level(content, "fighter", 10, random.Random(1))
    for stat in content.rules.stats:
        grown = job.stat_growth.get(stat, 0)
        gained = tenth.base[stat] - first.base[stat]
        assert int(grown * 0.75) * 9 <= gained <= int(grown) * 9, f"{stat} grows 75 to 100 percent of {grown} a level"
    assert [ability.key for ability in tenth.abilities] == ["power_strike", "cleave"]
    assert tenth.base == balance.hero_at_level(content, "fighter", 10, random.Random(1)).base, "the same dice make the same hero"
    assert (tenth.job, tenth.level, tenth.job_need) == ("fighter", 10, job.xp_needed)


def test_an_encounter_is_the_asked_size_and_strength(content):
    rng = random.Random(7)
    for target in (10, 40, 120):
        for _ in range(20):
            foes = balance.pick_encounter(content, target, (1, 3), rng)
            assert 1 <= len(foes) <= 3
            share = target / len(foes)
            for key in foes:
                assert abs(balance.monster_pxp(content, key) - share) <= share * balance.FIT or len(foes) == 1
    assert len(balance.pick_encounter(content, 10**6, (3, 5), rng)) == 1, "when nothing is near, the nearest monster stands alone"


def test_a_fight_is_played_and_tallied(content):
    team = ["fighter", "mage", "cleric", "scout"]
    # (the example game's monsters are weak, so the encounter is worth a quarter of the team: two or three of them fit)
    result = balance.run_fight(content, team, 3, random.Random("tally"), difficulty=0.25, sizes=(2, 3))
    assert result.outcome in ("won", "lost", "timeout")
    assert 2 <= len(result.foes) <= 3
    assert [hero.job for hero in result.heroes] == team
    assert result.rounds >= 1 and 0 <= result.life_left <= 1
    assert all(hero.turns >= 0 and hero.dealt >= 0 and hero.taken >= 0 for hero in result.heroes)
    if result.outcome == "won":
        assert sum(hero.dealt for hero in result.heroes) >= sum(content.monsters[key].stats["HP"] for key in result.foes), (
            "a won fight dealt at least all the foes' life (damage is counted before it is cut off at zero)"
        )
        assert result.life_left > 0
    for cast in result.casts:
        assert cast.job in team and cast.targets >= 0 and cast.enemy_share >= 0


def test_the_same_dice_play_the_same_fight(content):
    def play():
        return balance.run_fight(content, ["fighter", "mage", "cleric", "scout"], 4, random.Random("again"))

    assert play() == play()


def test_a_run_is_summed_up_by_level_and_job(content):
    results = balance.simulate(content, [1, 3], 3, seed=5)
    assert sorted(results) == [1, 3] and all(len(fights) == 3 for fights in results.values())
    report = balance.summarize(results)
    for level, row in report.items():
        assert row["fights"] == 3
        assert row["won"] + row["lost"] + row["timeout"] == pytest.approx(1)
        assert row["jobs"], "every job that fought is in the table"
        for stats in row["jobs"].values():
            assert 0 <= stats["fell"] <= 1 and 0 <= stats["damage_share"] <= 1
    assert balance.simulate(content, [1, 3], 3, seed=5) == results, "the seed makes the run repeat"
    text = balance.format_report(report)
    assert "Level 1:" in text and "Level 3:" in text


def test_the_command_line_writes_a_report_and_the_numbers(tmp_path, capsys):
    out = tmp_path / "numbers.json"
    code = balance.main(["--content", str(SEED), "--levels", "2", "--fights", "2", "--team", "fighter,mage", "--foes", "1-2", "--json", str(out)])
    assert code == 0
    assert "Level 2: 2 fights" in capsys.readouterr().out
    saved = json.loads(out.read_text())
    assert list(saved["levels"]) == ["2"] and set(saved["levels"]["2"]["jobs"]) == {"fighter", "mage"}


@pytest.mark.parametrize(
    "arguments, message",
    [
        (["--team", "fighter,wizard"], "there is no job 'wizard'"),
        (["--foes", "5-2"], "--foes wants fewest-most"),
        (["--rules", "nonsense"], "--rules wants module:Class"),
        (["--rules", "terraforma.tools.balance:BalanceError"], "is not a terraforma Rules class"),
    ],
)
def test_the_command_line_says_what_is_wrong(arguments, message, capsys):
    assert balance.main(["--content", str(SEED), "--levels", "1", "--fights", "1", *arguments]) == 1
    assert message in capsys.readouterr().err
