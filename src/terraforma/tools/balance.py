"""Balance simulator for game makers: whole teams of heroes against the monsters of their level, played with the engine's own rules.

A game maker asks "is each class about as effective in its role through the game?". This plays a team of heroes (all of one
level, with their job's abilities and no gear) against a random encounter whose strength matches the team's, many times at each
of several levels, and reports what each job did: damage dealt, damage taken, healing, mana spent, how often it fell, and how much of
the enemy party's life each ability took per cast.

It is a tool, run on demand, and no test calls it for more than a few fights. It never imports a game: the game's seed folder is
read from disk, and its rules class (if it has one) is named on the command line.

    python -m terraforma.tools.balance --content src/my_game/seed --rules my_game.rules:MyRules \\
        --levels 1,5,10,20,30,40,50 --fights 200 --team knight,cleric,rogue,tutor --json out.json

Without ``--team`` every fight draws four different jobs at random, so each job's numbers average over many teams.

What it does and does not model (so a number is read for what it is):
- A hero at level L is made the way the engine makes one: the job's growth rounded for level 1, then each level adds a random 75 to
  100 percent of the growth, rounded down (``Rules.advance``). It wears nothing and carries nothing.
- Heroes and monsters are played by the engine's AI, with the numbers it gives a player's hero of that level (``fights.ai``).
- Damage is credited to whoever's turn it is. Damage from a status that ticks during someone else's turn is credited to them.
- Every fight has its own stream of dice, taken from ``--seed``, level and fight number, so a run repeats exactly.
"""

import argparse
import importlib
import json
import random
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace

from ..content.schema import check_seed
from ..fights import ai
from ..fights.combatant import Address, Combatant
from ..fights.content import ability_spec, monster_combatant, status_spec
from ..fights.events import Event, EventType
from ..fights.fight import Fight, build_fight
from ..fights.resolve import do_combat
from ..fights.rules import Rules
from ..seed import load_seed

HEROES, FOES = 0, 1
#: A group holds five fighters; a bigger side is split into groups of five.
GROUP_SIZE = 5
#: How many monsters an encounter has, as (fewest, most) at the first level and at the last level of the sweep. Mike's rule for Vanguard:
#: early parties field 3 to 5, and the end game anything from 4 very strong monsters to three nearly full groups. In between the
#: bounds move in a straight line with the level. ``--foes 3-5`` fixes it for every level.
SIZE_AT_START, SIZE_AT_END, LAST_LEVEL = (3, 5), (4, 14), 50
#: A monster fits a spot in the encounter if its PXP is within this share of the spot's.
FIT = 0.3


class BalanceError(ValueError):
    pass


@dataclass
class Content:
    """What the simulation reads: the game's checked seed and its rules."""

    rules: Rules
    jobs: dict[str, SimpleNamespace]
    monsters: dict[str, SimpleNamespace]
    abilities: dict[str, SimpleNamespace]
    items: dict[str, SimpleNamespace]
    statuses: dict
    pxp: dict[str, int] = field(default_factory=dict)  # a monster's strength by the rules, kept once worked out


def rules_from(path: str | None) -> Rules:
    """The rules class named as ``module:Class`` (the engine's own ``Rules`` when none is named)."""
    if not path:
        return Rules()
    module, _, name = path.partition(":")
    if not module or not name:
        raise BalanceError(f"--rules wants module:Class, not {path!r}")
    rules = getattr(importlib.import_module(module), name)()
    if not isinstance(rules, Rules):
        raise BalanceError(f"{path} is not a terraforma Rules class")
    return rules


def _rows(checked: list) -> dict[str, SimpleNamespace]:
    return {row.key: SimpleNamespace(**row.model_dump()) for row in checked}


def load_content(seed_dir: Path, rules: Rules | None = None) -> Content:
    """The game's seed folder, checked, as the fights read it."""
    rules = rules or Rules()
    checked = check_seed(load_seed(seed_dir), rules.stats, rules.resource_names)
    statuses = {row.key: status_spec(SimpleNamespace(**row.model_dump())) for row in checked["statuses"]}
    return Content(rules, _rows(checked["jobs"]), _rows(checked["monsters"]), _rows(checked["abilities"]), _rows(checked["items"]), statuses)


def hero_at_level(content: Content, job_key: str, level: int, rng: random.Random) -> Combatant:
    """A hero of the job at the level, as the engine would have made it by fighting its way there, wearing nothing."""
    job = content.jobs[job_key]
    rules = content.rules
    stats = {stat: round(job.stat_growth.get(stat, 0)) for stat in rules.stats}
    for _ in range(level - 1):
        for stat in rules.stats:
            stats[stat] += int(job.stat_growth.get(stat, 0) * (75 + rng.randint(0, 25)) / 100.0)
    known = [ability_spec(content.abilities[entry["ability"]]) for entry in job.abilities if entry["level"] <= level]
    hero = Combatant(
        name=job.name, base=dict(stats), current={stat: value for stat, value in stats.items() if stat in rules.resource_names},
        abilities=known, level=level, job=job.key, job_need=job.xp_needed, growth=dict(job.stat_growth),
    )
    ai.profile_for_level(level, known, player=True, rng=rng).apply(hero)
    return hero


def foe(content: Content, key: str) -> Combatant:
    fighter = monster_combatant(content.monsters[key], content.items, content.abilities, content.rules)
    fighter.monster = key
    return fighter


def monster_pxp(content: Content, key: str) -> int:
    if key not in content.pxp:
        content.pxp[key] = content.rules.pxp(foe(content, key))
    return content.pxp[key]


def encounter_sizes(level: int) -> tuple[int, int]:
    """The fewest and most monsters an encounter has at $level (see SIZE_AT_START)."""
    along = min(max((level - 1) / (LAST_LEVEL - 1), 0.0), 1.0)
    return tuple(round(start + (end - start) * along) for start, end in zip(SIZE_AT_START, SIZE_AT_END, strict=True))


def pick_encounter(content: Content, target_pxp: int, sizes: tuple[int, int], rng: random.Random) -> list[str]:
    """Monsters whose strengths add up to about $target_pxp, $sizes (fewest, most) of them: the number is drawn evenly among the numbers
    that have monsters of the right size, and each place is filled with a monster within FIT of an even share."""
    options = {}
    for count in range(sizes[0], sizes[1] + 1):
        share = target_pxp / count
        fitting = [key for key in sorted(content.monsters) if abs(monster_pxp(content, key) - share) <= share * FIT]
        if fitting:
            options[count] = fitting
    if not options:  # nothing is near: the single monster that is nearest
        return [min(sorted(content.monsters), key=lambda key: abs(monster_pxp(content, key) - target_pxp))]
    count = rng.choice(sorted(options))
    return [rng.choice(options[count]) for _ in range(count)]


def layout(heroes: list[Combatant], foes: list[Combatant]) -> dict[int, dict[int, list[Combatant]]]:
    def groups(side: list[Combatant]) -> dict[int, list[Combatant]]:
        return {index: side[start:start + GROUP_SIZE] for index, start in enumerate(range(0, len(side), GROUP_SIZE))}

    return {HEROES: groups(heroes), FOES: groups(foes)}


# --- what happened -----------------------------------------------------------------------------------------------

@dataclass
class HeroResult:
    job: str
    level: int
    turns: int = 0
    dealt: int = 0
    taken: int = 0
    healed: int = 0
    mana_spent: int = 0
    died: bool = False


@dataclass
class Cast:
    """One use of an ability (or a weapon attack): what it did to the enemy party and to the actor's own."""

    job: str
    ability: str
    damage: int
    targets: int
    enemy_share: float  # the damage as a share of the enemy party's life when the fight began
    healed: int
    own_share: float  # the healing as a share of the actor's party's life when the fight began


@dataclass
class FightResult:
    outcome: str  # "won", "lost" or "timeout"
    rounds: int
    foes: list[str]
    team_pxp: int
    foe_pxp: int
    life_left: float  # the heroes' life at the end as a share of the beginning
    heroes: list[HeroResult]
    casts: list[Cast]


class Tally:
    """Reads a fight's events, in order, and credits each hero with what its turns did."""

    def __init__(self, fight: Fight, rules: Rules):
        self.rules = rules
        self.heroes = {address: HeroResult(fight.get(address).job, fight.get(address).level) for address in fight.addresses() if address[0] == HEROES}
        self.life = {party: sum(fight.get(address).get_base(rules, rules.vital) for address in fight.addresses() if address[0] == party) for party in (HEROES, FOES)}
        self.casts: list[Cast] = []
        self.actor: Address | None = None
        self.open: dict | None = None

    def close(self) -> None:
        cast, self.open = self.open, None
        if cast is not None and self.actor in self.heroes:
            job = self.heroes[self.actor].job
            self.casts.append(Cast(
                job, cast["ability"], cast["damage"], len(cast["hit"]), cast["damage"] / max(self.life[FOES], 1),
                cast["healed"], cast["healed"] / max(self.life[HEROES], 1),
            ))

    def read(self, events: list[Event]) -> None:
        rules = self.rules
        for event in events:
            kind, data = event.type, event.data
            if kind is EventType.TURN:
                self.close()
                self.actor = tuple(data[:3])
                if self.actor in self.heroes:
                    self.heroes[self.actor].turns += 1
            elif kind in (EventType.SKILL, EventType.SPELL):
                self.open = {"ability": data[0], "damage": 0, "healed": 0, "hit": set()}
            elif kind is EventType.ATTACK:
                self.open = {"ability": f"attack:{data[0] or 'bare hands'}", "damage": 0, "healed": 0, "hit": set()}
            elif kind is EventType.ROUND_END:
                self.close()
                self.actor = None
            elif kind is EventType.ALTER_STAT:
                address, stat, amount = tuple(data[:3]), data[3], data[4]
                if stat == rules.mana and amount < 0 and address in self.heroes and address == self.actor:
                    self.heroes[address].mana_spent += -amount
            elif kind is EventType.DAMAGE:
                target, amount = tuple(data[:3]), data[3]
                if target in self.heroes:
                    self.heroes[target].taken += amount
                if self.actor is not None and self.actor[0] != target[0]:
                    if self.actor in self.heroes:
                        self.heroes[self.actor].dealt += amount
                    if self.open is not None:
                        self.open["damage"] += amount
                        self.open["hit"].add(target)
            elif kind is EventType.RESTORE and data[3] == rules.vital:
                target, amount = tuple(data[:3]), data[4]
                if self.actor is not None and self.actor[0] == target[0]:
                    if self.actor in self.heroes:
                        self.heroes[self.actor].healed += amount
                    if self.open is not None:
                        self.open["healed"] += amount
            elif kind is EventType.DIED and tuple(data[:3]) in self.heroes:
                self.heroes[tuple(data[:3])].died = True
        # a cast that is the last thing in the round's events is closed by the round end, or by the next fight's reset


def run_fight(content: Content, team: list[str], level: int, rng: random.Random, *, difficulty: float = 1.0,
              sizes: tuple[int, int] | None = None, max_rounds: int = 60) -> FightResult:
    """One fight: a team of heroes of $level against an encounter worth $difficulty times the team's strength, of $sizes monsters
    (fewest, most; what ``encounter_sizes`` says for the level if left out)."""
    rules = content.rules
    heroes = [hero_at_level(content, job, level, rng) for job in team]
    team_pxp = sum(rules.pxp(hero) for hero in heroes)
    keys = pick_encounter(content, round(team_pxp * difficulty), sizes or encounter_sizes(level), rng)
    enemies = [foe(content, key) for key in keys]
    for enemy in enemies:
        ai.Profile(enemy.ai_action, enemy.ai_goal, enemy.ai_target, enemy.ai_experience).apply(enemy)
    fight = build_fight(layout(heroes, enemies), content.statuses)
    tally = Tally(fight, rules)
    rounds = 0
    while rounds < max_rounds and not fight.over:
        for address in fight.addresses():
            fighter = fight.get(address)
            if fighter.present(rules):
                ai.commit(fighter, ai.choose_command(rules, fight, address, rng))
        tally.read(do_combat(fight, rules, rng))
        rounds += 1
    tally.close()
    lost, won = fight.parties[HEROES].dead(rules), fight.parties[FOES].dead(rules)
    outcome = "won" if won and not lost else "lost" if lost else "timeout"
    left = sum(fight.get(address).current[rules.vital] for address in fight.addresses() if address[0] == HEROES)
    return FightResult(
        outcome, rounds, keys, team_pxp, sum(monster_pxp(content, key) for key in keys), left / max(tally.life[HEROES], 1),
        list(tally.heroes.values()), tally.casts,
    )


# --- many fights -------------------------------------------------------------------------------------------------

def simulate(content: Content, levels: list[int], fights: int, *, team: list[str] | None = None, seed: int = 1, difficulty: float = 1.0,
             sizes: tuple[int, int] | None = None, max_rounds: int = 60) -> dict[int, list[FightResult]]:
    """$fights fights at each of $levels: $team every time, or four different jobs drawn for each fight."""
    results: dict[int, list[FightResult]] = {}
    for level in levels:
        results[level] = []
        for number in range(fights):
            rng = random.Random(f"{seed}:{level}:{number}")
            chosen = team or rng.sample(sorted(content.jobs), min(4, len(content.jobs)))
            results[level].append(run_fight(content, chosen, level, rng, difficulty=difficulty, sizes=sizes, max_rounds=max_rounds))
    return results


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def summarize(results: dict[int, list[FightResult]]) -> dict:
    """The numbers of a run, by level: how the fights went, and for each job what it did per fight it was in."""
    report = {}
    for level, fights in results.items():
        jobs: dict[str, list[tuple[HeroResult, float]]] = defaultdict(list)
        casts: dict[tuple[str, str], list[Cast]] = defaultdict(list)
        for result in fights:
            team_dealt = sum(hero.dealt for hero in result.heroes) or 1
            for hero in result.heroes:
                jobs[hero.job].append((hero, hero.dealt / team_dealt))
            for cast in result.casts:
                casts[(cast.job, cast.ability)].append(cast)
        report[level] = {
            "fights": len(fights),
            "won": _mean([result.outcome == "won" for result in fights]),
            "lost": _mean([result.outcome == "lost" for result in fights]),
            "timeout": _mean([result.outcome == "timeout" for result in fights]),
            "rounds": _mean([result.rounds for result in fights]),
            "life_left": _mean([result.life_left for result in fights]),
            "jobs": {
                job: {
                    "fights": len(rows), "damage": _mean([hero.dealt for hero, _ in rows]), "damage_share": _mean([share for _, share in rows]),
                    "taken": _mean([hero.taken for hero, _ in rows]), "healed": _mean([hero.healed for hero, _ in rows]),
                    "mana": _mean([hero.mana_spent for hero, _ in rows]), "fell": _mean([hero.died for hero, _ in rows]),
                    "turns": _mean([hero.turns for hero, _ in rows]),
                }
                for job, rows in sorted(jobs.items())
            },
            "abilities": [
                {
                    "job": job, "ability": ability, "casts": len(rows) / len(jobs[job]), "targets": _mean([cast.targets for cast in rows]),
                    "enemy_share": _mean([cast.enemy_share for cast in rows]), "own_share": _mean([cast.own_share for cast in rows]),
                }
                for (job, ability), rows in sorted(casts.items())
            ],
        }
    return report


def format_report(report: dict) -> str:
    lines = []
    for level, row in report.items():
        lines.append(
            f"Level {level}: {row['fights']} fights, won {row['won']:.0%}, lost {row['lost']:.0%}, timed out {row['timeout']:.0%}; "
            f"{row['rounds']:.1f} rounds; heroes ended with {row['life_left']:.0%} of their life"
        )
        lines.append(f"  {'job':16} {'fights':>6} {'damage':>8} {'share':>6} {'taken':>8} {'healed':>8} {'mana':>6} {'fell':>5}")
        for job, stats in row["jobs"].items():
            lines.append(
                f"  {job:16} {stats['fights']:6d} {stats['damage']:8.0f} {stats['damage_share']:6.0%} {stats['taken']:8.0f} "
                f"{stats['healed']:8.0f} {stats['mana']:6.0f} {stats['fell']:5.0%}"
            )
        wide = [entry for entry in row["abilities"] if entry["targets"] > 1.05 and entry["enemy_share"] > 0]
        if wide:
            lines.append("  Abilities that hit several foes (a cast's damage as a share of the enemy party's life):")
            for entry in sorted(wide, key=lambda entry: -entry["enemy_share"]):
                lines.append(f"    {entry['job']:16} {entry['ability']:24} {entry['enemy_share']:6.0%} over {entry['targets']:.1f} foes, {entry['casts']:.1f} casts a fight it is in")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m terraforma.tools.balance", description=__doc__.split("\n\n")[0])
    parser.add_argument("--content", type=Path, required=True, help="the game's seed folder")
    parser.add_argument("--rules", help="the game's rules class, module:Class (the engine's own if left out)")
    parser.add_argument("--levels", default="1,5,10,20,30,40,50", help="comma-separated hero levels (default %(default)s)")
    parser.add_argument("--fights", type=int, default=100, help="fights at each level (default %(default)s)")
    parser.add_argument("--team", help="comma-separated job keys; four different jobs are drawn for each fight if left out")
    parser.add_argument("--difficulty", type=float, default=1.0, help="the encounter's strength as a multiple of the team's (default %(default)s)")
    parser.add_argument("--foes", help="fewest-most monsters in an encounter, such as 3-5, at every level (by default 3-5 at level 1 rising to 4-14 at level 50)")
    parser.add_argument("--max-rounds", type=int, default=60, help="a fight that lasts longer is a timeout (default %(default)s)")
    parser.add_argument("--seed", type=int, default=1, help="the dice (default %(default)s)")
    parser.add_argument("--json", type=Path, help="also write the numbers here")
    args = parser.parse_args(argv)
    try:
        content = load_content(args.content, rules_from(args.rules))
        team = args.team.split(",") if args.team else None
        for job in team or []:
            if job not in content.jobs:
                raise BalanceError(f"there is no job {job!r}")
        levels = [int(level) for level in args.levels.split(",")]
        sizes = None
        if args.foes:
            fewest, _, most = args.foes.partition("-")
            sizes = (int(fewest), int(most or fewest))
            if not 1 <= sizes[0] <= sizes[1]:
                raise BalanceError("--foes wants fewest-most, such as 3-5")
        report = summarize(simulate(
            content, levels, args.fights, team=team, seed=args.seed, difficulty=args.difficulty, sizes=sizes, max_rounds=args.max_rounds,
        ))
    except (BalanceError, ValueError, KeyError) as error:
        print(f"balance: {error}", file=sys.stderr)
        return 1
    print(format_report(report))
    if args.json:
        args.json.write_text(json.dumps({"settings": {key: str(value) for key, value in vars(args).items()}, "levels": report}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
