"""The end of a fight: who earned what, the gold, and who advances.

``Rules.on_fight_end`` calls ``settle`` once ``Rules.fight_is_over`` holds. It
reads the experience debts the fight collected as it went (``Rules.gauge_moved``),
pays them out with DragonStar's experience tree (``fights.experience``), splits
the gold the dead enemies dropped between each party's teams, and lets
``Rules.advance`` level up whoever earned enough. All of it is events
(``XpEarned``, ``Gold``, ``LevelUp``) so the log still replays, and the fight is
changed as they are made, like every other change in a round.

What it leaves to the caller (the live fight, 5d): saving a hero's experience,
level, stats and its team's gold to the database from the fight's result.
"""

import random

from .combatant import Address
from .events import Event, EventType, event
from .experience import Debt, Fighter, Party, gold_per_team, process_experience
from .fight import Fight
from .replay import apply_events
from .rules import Rules


def experience_parties(rules: Rules, fight: Fight) -> list[Party]:
    """The fight as the experience tree reads it."""
    parties = []
    for index, party in fight.parties.items():
        allies, enemies = rules.alignment(fight, index)
        fighters = []
        for address in fight.addresses():
            if address[0] != index:
                continue
            fighter = fight.get(address)
            debts = [Debt((debt[0], debt[1], debt[2]), debt[3], debt[4]) for debt in fighter.xp_debts]
            fighters.append(Fighter(address, debts, fighter.charid, fighter.fled))
        parties.append(Party(index, fighters, party.dead(rules), frozenset(allies), frozenset(enemies), party.teams))
    return parties


def settle(rules: Rules, fight: Fight, rng: random.Random) -> list[Event]:
    """Pays out a finished fight. Returns its events, already applied to ``fight``."""
    result = process_experience(experience_parties(rules, fight))
    events: list[Event] = []

    earned: list[Address] = []
    for address in fight.addresses():
        amount = result.earned.get(address, 0)
        if amount:
            events.append(event(EventType.XP_EARNED, *address, amount))
            earned.append(address)

    for index, party in fight.parties.items():
        if not party.teams:
            continue
        _, enemies = rules.alignment(fight, index)
        dropped = [
            fight.get(address).gold
            for address in fight.addresses()
            if address[0] in enemies and not fight.get(address).alive(rules)
        ]
        # only a team with a hero alive at the end (one who fled counts) is paid; a team that is all down gets no gold
        paid = [team for team in party.teams if fight.team_survives(index, team, rules)]
        share = gold_per_team(dropped, len(paid))
        if share:
            events.extend(event(EventType.GOLD, index, team, share) for team in paid)

    apply_events(fight, rules, events)  # experience first: advancing reads it
    for address in earned:
        level_ups = rules.advance(fight, address, rng)
        apply_events(fight, rules, level_ups)
        events.extend(level_ups)
    events.extend(rules.roll_drops(fight, rng))  # after the experience and the gold, so adding it changes nothing before it
    for index, party in fight.parties.items():
        if party.teams and party.lost(rules):
            lost = [event(EventType.PARTY_LOST, index), *rules.party_lost(fight, index)]
            events.extend(lost)
    return events
