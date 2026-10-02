"""Asking a player to rate another team after a fight.

When a fight between player teams ends, a team that was helped or harmed by another player team (a healed it, a nuked it)
and has no opinion of it yet is asked to rate it: a prompt, kept until the player answers or dismisses it. The engine
changes nothing by itself: an answer is an ordinary change to the relationship, so ``Relations.resolve`` still decides
the score that results, and ``Relations.ask_after_fight`` lets a game decide whom to ask at all.

Who interacted with whom is read off the experience debts the fight kept (``Rules.gauge_moved`` and
``Rules.status_acted``): a debt of a fighter to someone on another player team means that team harmed it (positive) or
helped it (negative). Monsters and teams of the same party's own side are not asked about.

A game's own rule can ask too: when ``Rules.relation_moved`` returns ``AskPlayer(delta, reason)``, the fight's log holds a
``RelationPrompt`` event and the actor's team is asked whether to change its view by that much. That question is always
asked (the game said so), carries the suggestion and the reason, and can be accepted as it stands or answered with a score.
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


async def asked_by_the_game(session: AsyncSession, record) -> dict[tuple[int, int], tuple[int, str]]:
    """What the game's own rule asked in a finished fight: ``{(team, other team): (suggested change, reason)}``, a team's
    suggestions for another added up and the last reason kept (the fight's ``RelationPrompt`` events)."""
    from ..fights import store
    from ..fights.events import Event, EventType

    asked: dict[tuple[int, int], tuple[int, str]] = {}
    for action in await store.actions(session, record):
        for raw in action.events:
            each = Event.from_list(raw)
            if each.type is EventType.RELATION_PROMPT:
                team, other, delta, reason = each.data
                asked[(team, other)] = (asked.get((team, other), (0, ""))[0] + delta, reason)
    return asked


async def create_prompts(session: AsyncSession, relations: Relations, record, fight) -> list[RatingPrompt]:
    """Puts the questions a finished fight raises (see the module's text): those the game's rule asked, then those the
    debts raise for a neutral team the game agrees to ask. Safe to call twice: a team is asked once per fight about another."""
    seen = interactions(fight)
    made = []
    wanted: list[tuple[int, int, str, int | None, str]] = [
        (team, other, seen.get((team, other), "asked"), delta, reason) for (team, other), (delta, reason) in sorted((await asked_by_the_game(session, record)).items())
    ]
    for (subject_team, object_team), interaction in sorted(seen.items()):
        subject, object = Ref("team", subject_team), Ref("team", object_team)
        row = await service.get(session, subject, object)
        score = row.score if row is not None else await relations.initial(session, subject, object)
        if await relations.ask_after_fight(session, subject, object, interaction, score):
            wanted.append((subject_team, object_team, interaction, None, ""))
    for subject_team, object_team, interaction, suggested, reason in wanted:
        if await service.name_of(session, Ref("team", subject_team)) is None or await service.name_of(session, Ref("team", object_team)) is None:
            continue
        if subject_team == object_team or any(each.subject_team_id == subject_team and each.object_team_id == object_team for each in made):
            continue
        if await session.scalar(select(RatingPrompt.id).where(
            RatingPrompt.fight_id == record.id, RatingPrompt.subject_team_id == subject_team, RatingPrompt.object_team_id == object_team
        )):
            continue
        prompt = RatingPrompt(fight_id=record.id, subject_team_id=subject_team, object_team_id=object_team, interaction=interaction,
                              suggested=suggested, reason=reason)
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
        {"id": prompt.id, "fight": guid, "interaction": prompt.interaction, "suggested": prompt.suggested, "reason": prompt.reason,
         "team": {"id": team_id, "name": team_name},
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


async def answer(session: AsyncSession, relations: Relations, account_id: int, prompt_id: int, score: int | None = None, accept: bool = False):
    """The player rates the other team: an ordinary change to their relationship (so the game's ``resolve`` decides the
    score that results), then the question is closed. With ``accept`` the change is the one the game's rule suggested
    (only a question the game's rule asked has one). Returns the relationship."""
    prompt = await _own_open(session, account_id, prompt_id)
    if accept == (score is not None):
        raise service.RelationError("answer with a score, or accept the suggestion")
    if accept and prompt.suggested is None:
        raise service.RelationError("there is no suggestion to accept")
    row = await service.apply(session, relations, Change(
        Ref("team", prompt.subject_team_id), Ref("team", prompt.object_team_id), score=score, delta=prompt.suggested if accept else None,
        by="player", reason=prompt.reason or f"rated after a fight in which they {prompt.interaction}",
    ))
    prompt.state = ANSWERED
    await session.flush()
    return row


async def dismiss(session: AsyncSession, account_id: int, prompt_id: int) -> None:
    """The player has no rating to give: the question is closed and nothing changes."""
    prompt = await _own_open(session, account_id, prompt_id)
    prompt.state = DISMISSED
    await session.flush()
