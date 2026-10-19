"""Talking to another party: what the hero's side could do with it, without doing any of it.

A party in reach for ``talk`` offers two things, each with whether it is on offer now and, if not, why. ``fight`` is the PvP the game
asks about first (the answer is ``POST /api/pvp/fights``, which runs the same checks, ``pvp.service.check_fight``); ``help`` is using
an item on one of its heroes (``terraforma.reach.helping``). An enemy party offers a fight, a friendly one help, and a mixed one (its
teams hold both stances) both, as the stance ``Rules.party_stance`` makes of the teams' relationships. Talking changes nothing,
so a pair of teams that never met is looked at with the score a new relationship would start at (``Relations.initial``) and no
relationship is formed.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.rules import Rules
from ..heroes.models import Hero, TeamMember
from ..parties import service as parties
from ..pvp.hooks import PvpZones
from ..pvp.service import PvpError, check_fight
from ..relations import service as relationships
from ..relations.hooks import Ref, Relations
from .hooks import HELP, TALK, Reach
from .service import _shown_teams, party_of_hero


class TalkError(ValueError):
    """The hero can't talk to that party: the message says why."""


class NoSuchParty(TalkError):
    pass


async def stance_toward(session: AsyncSession, rules: Rules, relations: Relations, mine: list[int], theirs: list[int]) -> str:
    """The stance of the teams $mine toward the teams $theirs (``Rules.party_stance``), reading the relationships there are and counting
    a missing one at ``Relations.initial`` instead of forming it."""
    stances = []
    for team in mine:
        for other in theirs:
            one, two = Ref("team", team), Ref("team", other)
            row = await relationships.get(session, one, two)
            stances.append(relations.stance(row.score if row is not None else await relations.initial(session, one, two)))
    return rules.party_stance(stances)


async def talk_to_party(
    session: AsyncSession, reach: Reach, zones: PvpZones, rules: Rules, relations: Relations, hero: Hero, party_id: int
) -> dict:
    """What the hero's side can do with party $party_id: ``{"party_id", "teams", "stance", "options"}``, where ``teams`` are the party's visible
    teams and ``options`` lists ``{"action", "available", "reason"}`` for ``fight`` and ``help``. Raises NoSuchParty, or TalkError when the
    hero's own party was asked for, the hero is on no team, or the party is not in reach for a talk."""
    try:
        party = await parties.get_party(session, party_id)
    except parties.NotFound as error:
        raise NoSuchParty(str(error)) from error
    mine = await party_of_hero(session, hero)
    if mine is not None and mine.id == party.id:
        raise TalkError("that is your own party")
    if reason := await reach.party(session, TALK, hero, party):
        raise TalkError(reason)
    team_id = await session.scalar(select(TeamMember.team_id).where(TeamMember.hero_id == hero.id))
    if team_id is None:
        raise TalkError("put the hero on a team first")
    stance = await stance_toward(session, rules, relations, [team_id], await parties.team_ids(session, party.id))
    fight = rules.may_challenge(stance)
    if fight is None and mine is None:
        fight = "a team must be in a party to pick a fight"
    elif fight is None:
        try:
            await check_fight(session, zones, rules, reach, mine, party)
        except PvpError as error:
            fight = str(error)
    helping = await reach.party(session, HELP, hero, party) or rules.may_help(stance)
    return {
        "party_id": party.id,
        "teams": await _shown_teams(session, party.id),
        "stance": stance,
        "options": [
            {"action": "fight", "available": fight is None, "reason": fight},
            {"action": "help", "available": helping is None, "reason": helping},
        ],
    }
