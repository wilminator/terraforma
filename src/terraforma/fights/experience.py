"""The experience tree: who earned what from whom, and who gets paid.

Pure functions, no database or web code. They carry DragonStar's rules over
as the engine's defaults ("the process mirrored, functionally equivalent").

How it works:

* Every time a fighter's HP or MP moves, the fighter that moved it earns a
  *debt* from the fighter it happened to: ``ratio`` is the share of the
  gauge that moved (positive: harm, negative: healing), and ``pxp`` is the
  debtor's own power (``Rules.pxp``), so beating something strong pays more.
  The debt belongs to the one who *owes* experience (the target of the effect);
  ``creditor`` is the one who earns.
* A debtor can owe at most 1.0 in total: if its debts add up to more, they
  are all scaled down, so a fighter that is hit a lot is not worth more than
  one that is hit once for its whole HP.
* Credit is only paid for good actions: harm to a non-ally, healing of a
  non-enemy. It is paid when a party leaves the fight (is wiped out), for
  the debts that party owes and is owed, and when the survivors are all
  friends, for everyone left.
* A credit becomes experience in three parts: half goes to the earner, 20%
  of the party's credit is pooled across the whole party (non-team members
  dilute it), and 30% of each team's own credit is pooled across that team.
  Fighters on no team (monsters, NPCs) earn nothing, but still count.

Fighters are addressed by ``(party, group, character)``, as ``FighterRef``
does; the tuple alias below is the plain form of it.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .combatant import Address
from .gear import round_half_up


@dataclass(frozen=True)
class Debt:
    """What a fighter owes the one who affected it: ``ratio`` of a gauge, at the debtor's own ``pxp``."""

    creditor: Address
    ratio: float
    pxp: int


@dataclass(frozen=True)
class Fighter:
    address: Address
    debts: Sequence[Debt] = ()
    #: The hero's id, if it is played by someone. None: a monster or NPC, which earns no experience.
    charid: int | None = None
    #: Whether it left the fight (fled, or was ejected): it keeps what it earned itself, but not its share of the party's
    #: and its team's pools. The others' pools are worked out as if it had stayed.
    fled: bool = False


@dataclass(frozen=True)
class Party:
    index: int
    fighters: Sequence[Fighter]
    #: Whether every fighter is down (HP below 1) when the fight ends.
    dead: bool
    allies: frozenset[int]
    enemies: frozenset[int]
    #: team id -> the charids on that team
    teams: Mapping[int, Sequence[int]] = field(default_factory=dict)


@dataclass
class Credits:
    """Credit earned per party: a running total, a headcount and each fighter's own."""

    total: int = 0
    count: int = 0
    per_fighter: dict[Address, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ExperienceResult:
    #: Experience earned by each fighter on a team (monsters and NPCs are left out).
    earned: dict[Address, int]
    credits: dict[int, Credits]
    winners: list[int]
    losers: list[int]
    #: True when one party or none is left, or the ones left are all friends.
    over: bool


def debt_scales(parties: Sequence[Party]) -> dict[Address, float]:
    """Each fighter's scale: 1, or 1 / total owed when it owes more than 1."""
    scales = {}
    for party in parties:
        for fighter in party.fighters:
            owed = sum(abs(debt.ratio) for debt in fighter.debts)
            scales[fighter.address] = 1.0 / owed if owed > 1 else 1.0
    return scales


def _distribute(
    exiting: int,
    exited: Sequence[int],
    parties: Sequence[Party],
    scales: Mapping[Address, float],
    credits: dict[int, Credits],
) -> None:
    everyone = {party.index for party in parties}
    for party in parties:
        if party.index in exited:
            continue
        non_allies = everyone - party.allies
        non_enemies = everyone - party.enemies
        for fighter in party.fighters:
            for debt in fighter.debts:
                creditor_party, _, _ = debt.creditor
                if creditor_party in exited:
                    continue
                if exiting not in (creditor_party, party.index):
                    continue
                harmful = debt.ratio > 0 and creditor_party in non_allies
                helpful = debt.ratio < 0 and creditor_party in non_enemies
                if not (harmful or helpful):
                    continue
                xp = round_half_up(abs(debt.ratio) * debt.pxp * scales[fighter.address])
                credits[creditor_party].total += xp
                credits[creditor_party].per_fighter[debt.creditor] += xp


def process_experience(parties: Sequence[Party]) -> ExperienceResult:
    """Settle the tree at the end of a round: who has left, what was earned, is the fight over."""
    scales = debt_scales(parties)
    credits = {
        party.index: Credits(
            count=len(party.fighters),
            per_fighter={fighter.address: 0 for fighter in party.fighters},
        )
        for party in parties
    }

    winners = [party.index for party in parties]
    losers: list[int] = []
    for party in parties:
        if party.dead:
            _distribute(party.index, losers, parties, scales, credits)
            losers.append(party.index)
            winners.remove(party.index)

    by_index = {party.index: party for party in parties}
    all_friends = all(set(winners) <= by_index[index].allies | {index} for index in winners)
    if all_friends:
        for index in winners:
            _distribute(index, losers, parties, scales, credits)
            losers.append(index)

    earned = _award(parties, credits)
    return ExperienceResult(
        earned=earned,
        credits=credits,
        winners=winners,
        losers=losers,
        over=all_friends or len(winners) < 2,
    )


def _award(parties: Sequence[Party], credits: Mapping[int, Credits]) -> dict[Address, int]:
    earned: dict[Address, int] = {}
    for party in parties:
        party_credits = credits[party.index]
        pool = round_half_up(party_credits.total * 0.2 / party_credits.count) if party_credits.count else 0

        team_totals: dict[int, int] = {}
        for fighter in party.fighters:
            for team_id, members in party.teams.items():
                if fighter.charid is not None and fighter.charid in members:
                    team_totals[team_id] = team_totals.get(team_id, 0) + party_credits.per_fighter[fighter.address]
        team_pools = {
            team_id: round_half_up(team_totals.get(team_id, 0) * 0.3 / len(members))
            for team_id, members in party.teams.items()
            if members
        }

        for fighter in party.fighters:
            team_id = next(
                (
                    team_id
                    for team_id, members in party.teams.items()
                    if fighter.charid is not None and fighter.charid in members
                ),
                None,
            )
            if team_id is None:
                continue
            own = round_half_up(party_credits.per_fighter[fighter.address] * 0.5)
            earned[fighter.address] = own if fighter.fled else own + pool + team_pools[team_id]
    return earned


def bonus_experience(earned: int, share: float) -> int:
    """Extra experience as a share of what was earned (for beating a player's monsters); never negative."""
    return math.floor(max(0, earned) * share)


def gold_per_team(dead_enemy_gold: Sequence[int], team_count: int) -> int:
    """The gold of every dead enemy, split evenly (rounded down) between a party's teams."""
    if team_count < 1:
        return 0
    return sum(dead_enemy_gold) // team_count


def experience_needed_after(level: int, job_need: float, need: int) -> int:
    """The experience needed for the level after ``level``: ``need`` plus this level's step."""
    return need + round_half_up(job_need / 2.0 * (((level + 3) * (level + 2) / 2) - 1))
