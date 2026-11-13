"""Command line of the play tests (see the package docstring)."""

import argparse
import importlib
import json
import sys
import traceback

from . import cast as casting
from . import demo
from . import harness, report, scenarios


def load_game(spec: str):
    module, _, name = spec.partition(":")
    found = getattr(importlib.import_module(module), name or "GAME")
    return found() if callable(found) and not hasattr(found, "rules") else found


def run_one(game, chosen: scenarios.Scenario, opts: scenarios.Options, staged: bool) -> report.Result:
    result = report.Result(chosen.name, chosen.about)
    try:
        with harness.boot(game) as sim:  # (a fresh world for every scenario, so one can't colour another)
            if staged:
                demo.stage(sim)
            world = casting.discover(sim, opts.shop, opts.item)
            result.say(f"cast: realm {world.realm or '-'}, town {world.town or '-'}/{world.material or '-'}, hamlet {world.hamlet or '-'}, "
                       f"dormant {world.dormant or '-'}, shop {world.shop or '-'}/{world.item or '-'}")
            scenarios.need(world, *chosen.needs)
            chosen.run(sim, world, result, opts)
    except scenarios.Skip as why:
        result.skipped = str(why)
    except Exception:  # a crash is a failure of the run, reported with the scenario, not a reason to stop the others
        result.crashed = traceback.format_exc()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m terraforma.tools.playsim", description=__doc__)
    parser.add_argument("--game", help="module:attr of the game to play (default: the demo world on the example game)")
    parser.add_argument("-s", "--scenario", action="append", help="a scenario to run (repeatable; default: all)")
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    parser.add_argument("--hop", type=int, default=10, help="ticks between housekeeping passes")
    parser.add_argument("--limit", type=int, default=20_000, help="the most ticks a scenario waits for something to happen")
    parser.add_argument("--rounds", type=int, default=60, help="rounds of the long run")
    parser.add_argument("--seed", type=int, default=1, help="the long run's dice")
    parser.add_argument("--shop", default="", help="a shop key whose item depends on a supply (demo: general)")
    parser.add_argument("--item", default="", help="that item's key (demo: herb)")
    parser.add_argument("--json", help="also write the results to this file")
    parser.add_argument("-q", "--quiet", action="store_true", help="only the checks, not the story")
    args = parser.parse_args(argv)
    if args.list:
        for each in scenarios.SCENARIOS.values():
            print(f"{each.name:20} {each.about}")
        return 0
    unknown = [name for name in args.scenario or [] if name not in scenarios.SCENARIOS]
    if unknown:
        parser.error(f"no such scenario: {', '.join(unknown)} (see --list)")
    staged = args.game is None
    game = demo.DEMO_GAME if staged else load_game(args.game)
    shop, item = (args.shop, args.item) if args.shop else ((demo.SHOP, demo.SHOP_ITEM) if staged else ("", ""))
    opts = scenarios.Options(hop=args.hop, limit=args.limit, rounds=args.rounds, seed=args.seed, shop=shop, item=item)
    results = [run_one(game, scenarios.SCENARIOS[name], opts, staged) for name in args.scenario or scenarios.SCENARIOS]
    print(report.render(results, verbose=not args.quiet))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as out:
            json.dump(report.as_json(results), out, indent=2)
    return 1 if any(each.status == report.FAIL for each in results) else 0


if __name__ == "__main__":
    sys.exit(main())
