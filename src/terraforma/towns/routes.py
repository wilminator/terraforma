"""The town calls: a team's owner says when the team is ready to leave a town, comes back into it, or leaves the party.
Each is its own route. A party comes into a town and is put back together by the map (the service functions), not by a call."""

from fastapi import APIRouter, HTTPException, Request, status

from ..api.deps import ActingAccount, CurrentAccount, Db, GameRules, GameTowns, get_relations, get_rules, get_towns
from ..heroes import service as heroes
from ..heroes.routes import Id
from ..heroes.routes import refuse as refuse_hero
from ..parties.service import PartyError
from . import service

router = APIRouter(prefix="/api/teams/{team_id}/town")


def refuse(error: ValueError) -> HTTPException:
    if isinstance(error, (service.TownError, PartyError)):
        return HTTPException(status.HTTP_409_CONFLICT, str(error))
    return refuse_hero(error)


@router.get("")
async def town(team_id: Id, account: CurrentAccount, db: Db) -> dict:
    """Whether the team is in a town with its party, its own state and its party's: who is waiting, and who has told it
    they are ready to leave."""
    try:
        team = await heroes.own_team(db, account, team_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return await service.view(db, team.id)


@router.post("/ready")
async def ready(team_id: Id, account: ActingAccount, db: Db, towns: GameTowns, rules: GameRules) -> dict:
    """Leave Town: the team (with the teams it goes with) is ready to leave and waits for the rest of its party. The others are
    told; when every team is waiting, the party is put back together and its fight starts (``fight`` is its id, or null), unless
    the hub offers it a running fight to join instead (``offer`` is true; see the join-offer calls)."""
    try:
        team = await heroes.own_team(db, account, team_id)
        result, fight = await service.leave(db, towns, rules, team.id)
    except (heroes.HeroError, service.TownError) as error:
        raise refuse(error) from error
    return {"result": result, "fight": fight.id if fight else None, "offer": await service.join_offer(db, team.id), **await service.view(db, team.id)}


@router.post("/come-back")
async def come_back(team_id: Id, account: ActingAccount, db: Db) -> dict:
    """A waiting team goes back into the town: it is no longer waiting to leave."""
    try:
        team = await heroes.own_team(db, account, team_id)
        await service.come_back(db, team.id)
    except (heroes.HeroError, service.TownError) as error:
        raise refuse(error) from error
    return await service.view(db, team.id)


@router.post("/leave-party")
async def leave_party(team_id: Id, account: ActingAccount, db: Db) -> dict:
    """The team leaves its party for good, in the town. If the rest are all waiting, the party is whole again."""
    try:
        team = await heroes.own_team(db, account, team_id)
        await service.leave_party(db, team.id)
    except (heroes.HeroError, service.TownError, PartyError) as error:
        raise refuse(error) from error
    return await service.view(db, team.id)


@router.get("/join-offer")
async def join_offer(team_id: Id, account: CurrentAccount, db: Db) -> dict:
    """Whether the hub has offered the team's party a running fight to join and waits for its leader's answer. What the fight is
    is never said."""
    try:
        team = await heroes.own_team(db, account, team_id)
    except heroes.HeroError as error:
        raise refuse(error) from error
    return {"offer": await service.join_offer(db, team.id)}


async def _answer(request: Request, account, team_id: int, accept: bool) -> dict:
    """Answers the hub's offer in its own transaction, so what is pushed to the fight's watchers has been committed."""
    towns, rules, relations = get_towns(request), get_rules(request), get_relations(request)
    try:
        async with request.app.state.sessionmaker() as session, session.begin():
            team = await heroes.own_team(session, account, team_id)
            if accept:
                joined, fight = await service.accept_join(session, towns, rules, relations, account.id, team.id)
            else:
                joined, fight = False, await service.decline_join(session, towns, rules, account.id, team.id)
            answer = {"joined": joined, "fight": fight.id if fight else None, **await service.view(session, team.id)}
    except (heroes.HeroError, service.TownError, PartyError) as error:
        raise refuse(error) from error
    if joined:
        await request.app.state.fights.push(fight.id, {"type": "party_joined", "fight": fight.id})
    return answer


@router.post("/join-offer/accept")
async def accept_join(team_id: Id, request: Request, account: ActingAccount) -> dict:
    """The party's leader accepts the hub's offer: the party becomes the next party of the running fight and acts from its next
    round (``joined`` is true, ``fight`` its id). If that fight can't take it any more, the party's own fight starts instead
    (``joined`` is false)."""
    return await _answer(request, account, team_id, True)


@router.post("/join-offer/decline")
async def decline_join(team_id: Id, request: Request, account: ActingAccount) -> dict:
    """The party's leader declines the hub's offer: the party's own fight starts as usual (``fight`` is its id, or null)."""
    return await _answer(request, account, team_id, False)
