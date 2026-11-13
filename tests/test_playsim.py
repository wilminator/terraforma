"""The play tests of the living world (terraforma.tools.playsim). The tool is run on demand for long plays; here each scenario runs briefly on the
demo world, a planted bug is shown to be caught, and the report and the pending slice 5 scenarios are checked."""

import json

import pytest

from terraforma.realms import supply
from terraforma.tools.playsim import demo, features, report, scenarios
from terraforma.tools.playsim.__main__ import main, run_one

pytestmark = pytest.mark.platform

SHORT = scenarios.Options(rounds=8, limit=2_000, shop=demo.SHOP, item=demo.SHOP_ITEM)
IMPLEMENTED = [name for name in scenarios.SCENARIOS if name not in scenarios.PENDING]


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_each_scenario_passes_on_the_demo_world(name):
    result = run_one(demo.DEMO_GAME, scenarios.SCENARIOS[name], SHORT, staged=True)
    assert result.crashed == "" and result.skipped == "", result.crashed or result.skipped
    assert result.checks, "a scenario that checks nothing proves nothing"
    assert result.status == report.PASS, [(each.label, each.detail) for each in result.checks if each.status == report.FAIL]


def test_a_planted_bug_in_the_supply_clock_is_caught(monkeypatch):
    real = supply.level_at
    monkeypatch.setattr(supply, "level_at", lambda row, now: min(row.capacity, real(row, now) + 1))
    result = run_one(demo.DEMO_GAME, scenarios.SCENARIOS["supply-clock"], SHORT, staged=True)
    assert result.status == report.FAIL
    assert any("sum of what came in" in each.label and each.status == report.FAIL for each in result.checks)


def test_a_game_with_no_living_world_is_skipped_not_failed():
    from terraforma.example import GAME

    result = run_one(GAME, scenarios.SCENARIOS["supply-clock"], SHORT, staged=False)
    assert result.status == report.SKIP and "no realms" in result.skipped + " ".join(result.story)


def test_the_slice_5_scenarios_wait_until_the_engine_has_them(monkeypatch):
    for name in scenarios.PENDING:
        result = run_one(demo.DEMO_GAME, scenarios.SCENARIOS[name], SHORT, staged=True)
        assert result.status == report.SKIP and "not on this engine yet" in result.skipped
    monkeypatch.setitem(features.PROBES, "ruins-closed", lambda sim: "Towns.is_open")
    result = run_one(demo.DEMO_GAME, scenarios.SCENARIOS["ruins-closed"], SHORT, staged=True)
    assert "Towns.is_open" in result.skipped and "not written yet" in result.skipped, "found, but no test body yet: said so, not passed"


def test_the_command_line_lists_runs_and_writes_json(tmp_path, capsys):
    assert main(["--list"]) == 0
    assert "fall-and-found" in capsys.readouterr().out
    out = tmp_path / "result.json"
    assert main(["-s", "supply-clock", "-q", "--json", str(out)]) == 0
    assert "1 passed" in capsys.readouterr().out
    assert json.loads(out.read_text())[0]["status"] == "pass"
    with pytest.raises(SystemExit):
        main(["-s", "no-such-scenario"])


def test_the_report_tables_line_up():
    lines = report.table(["tick", "full %"], [[1, 100], [20, 5]])
    assert len({len(each) for each in lines}) == 1
