"""Joining a running fight: the hub's offer, and a party becoming the next party of a fight.

When a party that is whole again is about to fight at the hub (``towns.service.leave``), the hub may, on a small chance
(``Rules.join_chance``), offer it a running fight to join instead (``offer``): one picked at random from those whose
strength suits the party (``Rules.may_join_fight``) and that have room (``Rules.max_fight_parties``). The players are not told
what is in it. The party's leader accepts (``join``) or declines (the hub then starts the party's fight as it always does).

Joining makes the party the next party of that fight (parties never merge). Between two rounds the fight gains a party: a
``PartyJoined`` event, kept with the fight (``store.add_party``) and written first into the log of the next round that plays,
so the log still replays and its hash chain covers it. The joiners act in that round. What they earn is only for what they
do after joining (the experience tree reads the debts the fight collects as it goes), and a joiner's fight clock is the
fight's own (it was fixed when the fight started).

How the parties stand to each other is decided as the party joins: every pair of teams, one from the party and one from a
party of the fight, that has no relationship yet gets one in both directions, neutral (``Relations.initial``). Each team's
view counts at face value (``Relations.stance``), and ``Rules.party_stance`` makes of those a party's stance toward another
party (any allies and no enemies: ally; enemies and no allies: enemy; both: neutral; none, as with strangers and monsters:
enemy). It is one way: the joiner may count a party as an ally while that party counts the joiner as an enemy.
"""

import random

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Team
from ..parties import service as parties
from ..relations import service as relationships
from ..relations.hooks import Change, Ref, Relations
from ..relations.service import RelationError
from . import live, store
from .build import team_party
from .events import Event, EventType
from .fight import Fight, Group, Party
from .models import FightParticipant, FightRecord, JoinOffer
from .rules import Rules
from .state import dehydrate_party


def strength(rules: Rules, fighters) -> int:
    """A party's strength: the sum of its fighters' potential experience."""
    return sum(rules.pxp(fighter) for fighter in fighters)


def parties_strength(rules: Rules, fight: Fight) -> list[int]:
    """The strength of each of the fight's parties as it stands (in the fight's order)."""
    return [strength(rules, [fight.get(address) for address in fight.addresses() if address[0] == index]) for index in fight.parties]


def refusal(rules: Rules, fight: Fight, record: FightRecord, joiner_strength: int) -> str | None:
    """Why a party of $joiner_strength may not join the running fight, or None if it may."""
    if record.finished or fight.over:
        return "That fight is over."
    if len(fight.parties) >= rules.max_fight_parties:
        return "That fight is full."
    return rules.may_join_fight(joiner_strength, parties_strength(rules, fight))


async def offer_of(session: AsyncSession, party_id: int) -> JoinOffer | None:
    return await session.get(JoinOffer, party_id)


async def withdraw(session: AsyncSession, party_id: int) -> bool:
    """Takes back the party's offer (answered, or the party has gone). True if there was one."""
    result = await session.execute(delete(JoinOffer).where(JoinOffer.party_id == party_id))
    return result.rowcount > 0


async def offer(session: AsyncSession, rules: Rules, party_id: int, joiner_strength: int, stream: random.Random) -> JoinOffer | None:
    """The hub's chance to offer the party a running fight: one roll of $stream (a stream of the world's) against
    ``Rules.join_chance``, then, from at most ``Rules.join_looked_at`` running fights picked at random, the first that suits. None
    when the roll misses or none suits. The party's offer stands until it is answered (``withdraw``)."""
    if (standing := await offer_of(session, party_id)) is not None:
        return standing
    if stream.random() >= rules.join_chance:
        return None
    ids = list((await session.scalars(select(FightRecord.id).where(FightRecord.finished.is_(False)).order_by(FightRecord.id))).all())
    stream.shuffle(ids)
    for fight_id in ids[:rules.join_looked_at]:
        record = await session.get(FightRecord, fight_id)
        fight, _played = await store.load_state(session, record, rules)
        if refusal(rules, fight, record, joiner_strength) is None:
            row = JoinOffer(party_id=party_id, fight_id=fight_id)
            session.add(row)
            await session.flush()
            return row
    return None


async def _score(session: AsyncSession, relations: Relations, subject: int, object: int) -> int:
    """A team's score for another, forming the relationship (neutral, as the game starts them) if there is none."""
    one, other = Ref("team", subject), Ref("team", object)
    row = await relationships.get(session, one, other)
    if row is None:
        try:
            async with session.begin_nested():
                row = await relationships.apply(session, relations, Change(one, other, delta=0, by="game", reason="join"))
        except RelationError:  # a team that has gone, or one the game won't let them form
            return await relations.initial(session, one, other)
    return row.score


async def _stance(session: AsyncSession, rules: Rules, relations: Relations, mine: list[int], theirs: list[int]) -> str:
    """How the teams $mine count the teams $theirs together (``Rules.party_stance``), forming the relationships they lack."""
    return rules.party_stance([relations.stance(await _score(session, relations, team, other)) for team in mine for other in theirs])


async def join(session: AsyncSession, rules: Rules, relations: Relations, fight_id: int, party_id: int) -> tuple[FightRecord, int]:
    """The party becomes the next party of the running fight: returns the fight and the party's number in it. It acts from the
    next round that plays. Raises Refused for a fight that is over, full or out of the party's range, a party with no heroes, or a
    hero of it that is in a fight already."""
    record = await session.scalar(select(FightRecord).where(FightRecord.id == fight_id).with_for_update())  # (one joiner at a time)
    if record is None:
        raise live.Refused("there's no such fight")
    fighters, teams = [], {}
    for team_id in await parties.team_ids(session, party_id):
        members, ids = await team_party(session, await session.get(Team, team_id), rules)
        fighters += members
        teams |= ids
    if not fighters:
        raise live.Refused("that party has no heroes")
    hero_ids = [hero for ids in teams.values() for hero in ids]
    if await session.scalar(
        select(FightParticipant.id).join(FightRecord, FightRecord.id == FightParticipant.fight_id)
        .where(FightParticipant.hero_id.in_(hero_ids), FightRecord.finished.is_(False)).limit(1)
    ) is not None:
        raise live.Refused("a hero of that party is already in a fight")
    fight, _played = await store.load_state(session, record, rules)
    if reason := refusal(rules, fight, record, strength(rules, fighters)):
        raise live.Refused(reason)
    number = max(fight.parties) + 1
    allies, enemies, others = set(), set(), {}
    mine = list(teams)
    for index, party in fight.parties.items():
        yours = list(party.teams)
        if (stance := await _stance(session, rules, relations, mine, yours)) != "neutral":
            (allies if stance == "ally" else enemies).add(index)
        before_allies, before_enemies = rules.alignment(fight, index)
        kept_allies, kept_enemies = set(before_allies) - {index}, set(before_enemies)
        if (stance := await _stance(session, rules, relations, yours, mine)) != "neutral":
            (kept_allies if stance == "ally" else kept_enemies).add(number)
        others[str(index)] = [sorted(kept_allies), sorted(kept_enemies)]
    joined = Party({group: Group(dict(enumerate(members))) for group, members in live._groups(fighters, rules.group_size).items()}, allies, enemies, teams)
    await store.add_party(session, record, fight, rules, Event(EventType.PARTY_JOINED, (number, dehydrate_party(number, joined), others)))
    return record, number
