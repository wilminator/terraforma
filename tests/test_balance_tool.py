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


# --- named teams: attrition and endurance ------------------------------------------------------------------------

TEAMS = {"usual": ["fighter", "mage", "cleric", "scout"], "no healer": ["fighter", "fighter", "mage", "scout"]}


def test_a_fight_reports_what_it_cost_the_party(content):
    result = balance.run_fight(content, TEAMS["usual"], 3, random.Random("cost"), difficulty=0.25, sizes=(2, 3))
    assert 0 <= result.life_lost <= 1 and 0 <= result.mana_lost <= 1
    assert result.life_lost == pytest.approx(1 - result.life_left, abs=1e-9), "a rested party's loss is what it has left, taken from 1"
    assert all(0 <= hero.life_lost <= 1 and 0 <= hero.mana_lost <= 1 for hero in result.heroes)


def test_a_hurt_party_fights_on_from_where_it_was(content):
    rng = random.Random("hurt")
    heroes = [balance.hero_at_level(content, job, 3, rng) for job in TEAMS["usual"]]
    heroes[0].current["HP"] = 0  # fallen
    heroes[1].current["HP"] = 1
    result = balance.run_fight(content, TEAMS["usual"], 3, rng, difficulty=0.25, sizes=(2, 3), heroes=heroes)
    assert heroes[0].current["HP"] == 0, "the fallen stay fallen"
    assert result.heroes[0].turns == 0 and result.heroes[0].dealt == 0
    assert result.life_lost >= 0


def test_an_endurance_run_ends_when_the_party_falls_or_the_cap_is_reached(content):
    run = balance.endurance(content, TEAMS["usual"], 3, random.Random("run"), cap=5, difficulty=0.25, sizes=(2, 3))
    assert 0 <= run.fights <= 5 and len(run.life_after) == run.fights
    assert run.wiped == (run.fights < 5 or not run.life_after)  # not wiped only when it came through all five
    assert all(0 < left <= 1 for left in run.life_after)
    assert balance.endurance(content, TEAMS["usual"], 3, random.Random("run"), cap=5, difficulty=0.25, sizes=(2, 3)) == run, "the dice repeat it"
    # a party that is never wiped out comes through every fight of the cap, and one that is wiped out does not
    for name in ("a", "b", "c", "d"):
        play = balance.endurance(content, TEAMS["usual"], 1, random.Random(name), cap=6, difficulty=3, sizes=(2, 6))
        assert (play.fights == 6) == (not play.wiped) or play.wiped and play.fights <= 6
        assert play.wiped or play.fights == 6


def test_the_two_modes_summarise_each_team_by_level(content):
    teams = {name: balance.Team.of(jobs) for name, jobs in TEAMS.items()}
    cost = balance.attrition(content, teams, [1, 3], 2, seed=3, difficulty=0.25, sizes=(2, 3))
    assert set(cost) == set(teams) and set(cost["usual"]) == {1, 3}
    assert all(0 <= row["life_lost"] <= 1 and 0 <= row["won"] <= 1 for levels in cost.values() for row in levels.values())
    lasted = balance.stamina(content, teams, [1, 3], 2, seed=3, cap=4, difficulty=0.25, sizes=(2, 3))
    for levels in lasted.values():
        for row in levels.values():
            assert 0 <= row["fewest"] <= row["median"] <= row["most"] <= 4 and 0 <= row["capped"] <= 1
    assert balance.attrition(content, teams, [1, 3], 2, seed=3, difficulty=0.25, sizes=(2, 3)) == cost
    assert "no healer" in balance.format_attrition(cost) and "no healer" in balance.format_stamina(lasted)


def test_a_hero_with_a_seed_is_the_same_hero_in_every_fight(content):
    seeds = [11, None, None, 12]
    first = balance.make_heroes(content, TEAMS["usual"], 8, random.Random("one"), seeds)
    second = balance.make_heroes(content, TEAMS["usual"], 8, random.Random("two"), seeds)
    assert first[0].base == second[0].base and first[3].base == second[3].base, "seeded heroes do not depend on the fight's dice"
    assert first[0].base == balance.hero_at_level(content, "fighter", 8, random.Random(11)).base
    assert first[1].base != second[1].base or first[2].base != second[2].base, "the others do"
    assert balance.make_heroes(content, TEAMS["usual"], 8, random.Random("one")) == balance.make_heroes(content, TEAMS["usual"], 8, random.Random("one"))


def scenario_file(tmp_path, text):
    path = tmp_path / "scenario.json"
    path.write_text(text if isinstance(text, str) else json.dumps(text))
    return path


def test_a_scenario_file_names_teams_levels_and_seeds(tmp_path, content):
    path = scenario_file(tmp_path, {
        "levels": [2, 4], "difficulty": 0.25, "runs": 3,
        "teams": {"usual": ["fighter", "mage"], "fixed": [{"job": "fighter", "seed": 5}, "cleric"]},
    })
    scenario = balance.load_scenario(path, content)
    assert scenario["levels"] == [2, 4] and scenario["runs"] == 3
    assert scenario["teams"] == {"usual": balance.Team(["fighter", "mage"], [None, None]), "fixed": balance.Team(["fighter", "cleric"], [5, None])}


def test_the_command_line_runs_a_scenario_in_both_modes(tmp_path, capsys):
    path = scenario_file(tmp_path, {"levels": [2], "difficulty": 0.25, "foes": "1-2", "fights": 2, "runs": 2, "cap": 3,
                                    "teams": {"usual": TEAMS["usual"], "fixed": [{"job": "fighter", "seed": 5}, "mage"]}})
    out = tmp_path / "numbers.json"
    base = ["--content", str(SEED), "--scenario", str(path), "--json", str(out)]
    assert balance.main([*base, "--mode", "attrition"]) == 0
    assert "What one fight costs a rested party" in capsys.readouterr().out
    saved = json.loads(out.read_text())
    assert set(saved["teams"]) == {"usual", "fixed"} and set(saved["teams"]["usual"]) == {"2"}
    assert balance.main([*base, "--mode", "endurance"]) == 0
    assert "How many fights a party lasts" in capsys.readouterr().out
    assert saved["teams"]["usual"]["2"]["fights"] == 2
    assert json.loads(out.read_text())["teams"]["usual"]["2"]["cap"] == 3
    # a flag given on the command line beats the file
    assert balance.main([*base, "--mode", "endurance", "--cap", "1", "--levels", "3"]) == 0
    assert set(json.loads(out.read_text())["teams"]["usual"]) == {"3"} and json.loads(out.read_text())["teams"]["usual"]["3"]["cap"] == 1
    capsys.readouterr()
    # with no scenario, one team or four drawn jobs are played
    assert balance.main(["--content", str(SEED), "--levels", "2", "--mode", "endurance", "--runs", "1", "--cap", "2", "--team", "fighter,mage"]) == 0
    assert "  team" in capsys.readouterr().out


@pytest.mark.parametrize(
    "text, message",
    [
        ("[]", "should be a JSON object"),
        ("{", "cannot read"),
        ('{"teams": {}}', "should be a JSON object"),
        ('{"teams": {"a": "fighter"}}', "should be a list of heroes"),
        ('{"teams": {"a": [3]}}', "a hero is a job key or"),
        ('{"teams": {"a": [{"job": "fighter", "seed": "x"}]}}', "a hero is a job key or"),
        ('{"teams": {"a": ["fighter", "wizard"]}}', "has no job 'wizard' to use"),
        ('{"teams": {"a": ["fighter"]}, "speed": 3}', "settings the tool does not know: speed"),
    ],
)
def test_the_command_line_says_what_is_wrong_with_a_scenario_file(tmp_path, capsys, text, message):
    path = scenario_file(tmp_path, text)
    assert balance.main(["--content", str(SEED), "--mode", "attrition", "--fights", "1", "--levels", "1", "--scenario", str(path)]) == 1
    assert message in capsys.readouterr().err


def test_a_scenario_is_not_for_the_report_mode(tmp_path, capsys):
    path = scenario_file(tmp_path, {"teams": {"a": ["fighter"]}})
    assert balance.main(["--content", str(SEED), "--levels", "1", "--fights", "1", "--scenario", str(path)]) == 1
    assert "pick one with --mode" in capsys.readouterr().err
