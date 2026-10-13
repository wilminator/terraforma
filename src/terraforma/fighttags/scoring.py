"""How a finished fight is scored for the teams that won it. Pure: no database.

Two scores, each worked out for a winning team of the fight:

- **overkill**: the team's overkill damage on killing blows, over the team's PXP (``Rules.pxp``, summed over its heroes);
- **underdog**: the PXP of the opposing parties, over the PXP of the team's own party.

A killing blow is credited to the fighter whose turn it is when the fighter dies, if it is on an enemy party; a kill by a status
that ticks outside anyone's turn (at the round's end) is credited to nobody. Both are scored from the fight as it ended, so
a hero's PXP is what it stood at then (a level gained in the fight counts). A fight is PvP for a team when a party that
is its enemy (``Rules.alignment``) holds a player's team; otherwise it is PvE. A team **won** when one of its heroes is
standing and every enemy party has gone (all down or fled).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from ..fights.events import Event, EventType
from ..fights.fight import Fight
from ..fights.rules import Rules

PVE_OVERKILL, PVE_UNDERDOG, PVP_OVERKILL, PVP_UNDERDOG = "pve_overkill", "pve_underdog", "pvp_overkill", "pvp_underdog"
#: The four tags a team can hold.
KINDS = (PVE_OVERKILL, PVE_UNDERDOG, PVP_OVERKILL, PVP_UNDERDOG)


@dataclass(frozen=True)
class TeamResult:
    team_id: int
    party: int
    pvp: bool
    won: bool
    overkill: float
    underdog: float

    def score(self, kind: str) -> float:
        """The score the team's fight has for $kind (0 for a kind of the other sort, PvE or PvP)."""
        if kind.startswith("pvp_") != self.pvp:
            return 0.0
        return self.overkill if kind.endswith("overkill") else self.underdog

    def kinds(self) -> tuple[str, str]:
        return (PVP_OVERKILL, PVP_UNDERDOG) if self.pvp else (PVE_OVERKILL, PVE_UNDERDOG)


def overkill_by_team(fight: Fight, rules: Rules, events: Iterable[Event]) -> dict[int, int]:
    """The overkill each team dealt with killing blows, from the whole fight's events in order."""
    dealt: dict[int, int] = {}
    actor = None
    for each in events:
        if each.type is EventType.TURN:
            actor = tuple(each.data[:3])
        elif each.type is EventType.ROUND_END:
            actor = None
        elif each.type is EventType.DIED and actor is not None:
            victim = tuple(each.data[:3])
            if actor[0] == victim[0] or victim[0] not in rules.alignment(fight, actor[0])[1]:
                continue  # not an enemy
            team = fight.team_of(actor)
            if team is not None:
                dealt[team] = dealt.get(team, 0) + max(0, int(each.data[4]))
    return dealt


def party_pxp(fight: Fight, rules: Rules, party: int) -> int:
    return sum(rules.pxp(fighter) for fighter in fight.parties[party].members())


def team_pxp(fight: Fight, rules: Rules, party: int, team: int) -> int:
    heroes = set(fight.parties[party].teams.get(team, []))
    return sum(rules.pxp(fighter) for fighter in fight.parties[party].members() if fighter.charid in heroes)


def team_results(fight: Fight, rules: Rules, events: Iterable[Event]) -> list[TeamResult]:
    """Every player's team in the finished fight, with what it scored (a team that did not win scores nothing)."""
    overkill = overkill_by_team(fight, rules, events)
    results = []
    for party, each in fight.parties.items():
        enemies = rules.alignment(fight, party)[1]
        pvp = any(fight.parties[other].teams for other in enemies)
        beaten = all(fight.parties[other].dead(rules) for other in enemies)
        opposing = sum(party_pxp(fight, rules, other) for other in enemies)
        own = party_pxp(fight, rules, party)
        for team in each.teams:
            won = beaten and bool(enemies) and fight.team_survives(party, team, rules)
            strength = team_pxp(fight, rules, party, team)
            results.append(TeamResult(
                team_id=team, party=party, pvp=pvp, won=won,
                overkill=overkill.get(team, 0) / strength if won and strength else 0.0,
                underdog=opposing / own if won and own else 0.0,
            ))
    return results
