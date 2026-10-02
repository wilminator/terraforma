"""Asking a player to rate another team after a fight.

When a fight between player teams ends, a team that was helped or harmed by another player team (a healed it, a nuked it)
and has no opinion of it yet is asked to rate it: a prompt, kept until the player answers or dismisses it. The engine
changes nothing by itself: an answer is an ordinary change to the relationship, so ``Relations.resolve`` still decides
the score that results, and ``Relations.ask_after_fight`` lets a game decide whom to ask at all.

Who interacted with whom is read off the experience debts the fight kept (``Rules.gauge_moved`` and
``Rules.status_acted``): a debt of a fighter to someone on another player team means that team harmed it (positive) or
helped it (negative). Monsters and teams of the same party's own side are not asked about.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Team
from . import service
from .hooks import Change, Ref, Relations
from .models import ANSWERED, BOTH, DISMISSED, HARMED, HELPED, PENDING, RatingPrompt


def interactions(fight) -> dict[tuple[int, int], str]:
    """Which player team did what to which, in a finished fight: ``{(affected team, acting team): helped | harmed | both}``,
    between teams of different parties only."""
    team_of: dict[int, tuple[int, int]] = {}
    for index, party in fight.parties.items():
        for team, members in party.teams.items():
            for charid in members:
                team_of[charid] = (index, team)
    kinds: dict[tuple[int, int], set[str]] = {}
    for address in fight.addresses():
        fighter = fight.get(address)
        if fighter.charid not in team_of:
            continue
        party, team = team_of[fighter.charid]
        for debt in fighter.xp_debts:
            if not debt[3]:
                continue
            actor = fight.get(tuple(debt[:3]))
            if actor.charid not in team_of or team_of[actor.charid][0] == party:
                continue
            kinds.setdefault((team, team_of[actor.charid][1]), set()).add(HARMED if debt[3] > 0 else HELPED)
    return {pair: BOTH if len(found) > 1 else next(iter(found)) for pair, found in kinds.items()}


async def create_prompts(session: AsyncSession, relations: Relations, fight_id: int, fight) -> list[RatingPrompt]:
    """Puts the questions a finished fight raises (see the module's text). Safe to call twice: a team is asked once per fight."""
    made = []
    for (subject_team, object_team), interaction in sorted(interactions(fight).items()):
        subject, object = Ref("team", subject_team), Ref("team", object_team)
        if await service.name_of(session, subject) is None or await service.name_of(session, object) is None:
            continue
        row = await service.get(session, subject, object)
        score = row.score if row is not None else await relations.initial(session, subject, object)
        if not await relations.ask_after_fight(session, subject, object, interaction, score):
            continue
        if await session.scalar(select(RatingPrompt.id).where(
            RatingPrompt.fight_id == fight_id, RatingPrompt.subject_team_id == subject_team, RatingPrompt.object_team_id == object_team
        )):
            continue
        prompt = RatingPrompt(fight_id=fight_id, subject_team_id=subject_team, object_team_id=object_team, interaction=interaction)
        session.add(prompt)
        made.append(prompt)
    await session.flush()
    return made


async def pending(session: AsyncSession, account_id: int, limit: int = 50) -> list[dict]:
    """The account's open questions, the oldest first, with the teams' names and the fight's public name."""
    from ..fights.models import FightRecord

    asking, asked = Team.__table__.alias("asking"), Team.__table__.alias("asked")
    rows = await session.execute(
        select(RatingPrompt, FightRecord.guid, asking.c.id, asking.c.name, asked.c.id, asked.c.name)
        .join(FightRecord, FightRecord.id == RatingPrompt.fight_id)
        .join(asking, asking.c.id == RatingPrompt.subject_team_id).join(asked, asked.c.id == RatingPrompt.object_team_id)
        .where(asking.c.account_id == account_id, RatingPrompt.state == PENDING).order_by(RatingPrompt.id).limit(limit)
    )
    return [
        {"id": prompt.id, "fight": guid, "interaction": prompt.interaction, "team": {"id": team_id, "name": team_name},
         "other": {"id": other_id, "name": other_name}}
        for prompt, guid, team_id, team_name, other_id, other_name in rows.all()
    ]


async def _own_open(session: AsyncSession, account_id: int, prompt_id: int) -> RatingPrompt:
    prompt = await session.get(RatingPrompt, prompt_id, with_for_update=True)
    team = await session.get(Team, prompt.subject_team_id) if prompt is not None else None
    if prompt is None or team is None or team.account_id != account_id:
        raise service.NotFound("there's no such question")
    if prompt.state != PENDING:
        raise service.RelationError("that question has been dealt with")
    return prompt


async def answer(session: AsyncSession, relations: Relations, account_id: int, prompt_id: int, score: int):
    """The player rates the other team: an ordinary change to their relationship (so the game's ``resolve`` decides the
    score that results), then the question is closed. Returns the relationship."""
    prompt = await _own_open(session, account_id, prompt_id)
    row = await service.apply(session, relations, Change(
        Ref("team", prompt.subject_team_id), Ref("team", prompt.object_team_id), score=score, by="player",
        reason=f"rated after a fight in which they {prompt.interaction}",
    ))
    prompt.state = ANSWERED
    await session.flush()
    return row


async def dismiss(session: AsyncSession, account_id: int, prompt_id: int) -> None:
    """The player has no rating to give: the question is closed and nothing changes."""
    prompt = await _own_open(session, account_id, prompt_id)
    prompt.state = DISMISSED
    await session.flush()
