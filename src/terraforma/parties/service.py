"""Forming parties out of teams, merging them and leaving them.

A party is a collection of *whole* teams: a team joins whole and leaves whole (never split), a team is in at
most one party, and the smallest party is one team. How many heroes a party holds is the game's rule
(``Rules.party_size``, 20 by default: four groups of five). A team joins only if there is a place for every
one of its heroes, and a hero added to a team that is in a party needs a place too (``check_room``).

A party has a leader, a player (account) rather than a team: the player who founded it, then whoever accepts another party
into theirs (``accepted_by`` on ``join_party`` and ``merge_parties``). When the leader's last team leaves, the leadership goes
to the owner of the party's first team. Nothing else follows from leading in the engine except who hands out the party's
``assign`` drops (``is_leader``, ``Rules.may_assign_drop``).

These are service functions, not calls: the map drives them later (an interaction on the map forms and merges
parties), so they take ids rather than an account, and whoever calls them decides who may. They count first,
so two calls at the same instant could each slip past the size limit; the database does refuse a team in two
parties, and a clash of positions.
"""

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..fights.models import JoinOffer
from ..heroes.models import Hero, Team, TeamMember
from ..world.start import ensure_start
from ..towns.models import TownTeam, TownVisit
from ..standing.models import PARTY, TEAM, StandingStatus
from .models import Party, PartyRequest, PartyTeam


class PartyError(ValueError):
    """Something the caller can fix: the message says what."""


class NotFound(PartyError):
    pass


async def team_size(session: AsyncSession, team_id: int) -> int:
    return await session.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team_id))


async def size(session: AsyncSession, party_id: int) -> int:
    """How many heroes the party holds, all its teams together."""
    return await session.scalar(
        select(func.count()).select_from(TeamMember).join(PartyTeam, PartyTeam.team_id == TeamMember.team_id).where(PartyTeam.party_id == party_id)
    )


async def party_of(session: AsyncSession, team_id: int) -> Party | None:
    return await session.scalar(select(Party).join(PartyTeam, PartyTeam.party_id == Party.id).where(PartyTeam.team_id == team_id))


async def get_party(session: AsyncSession, party_id: int) -> Party:
    party = await session.get(Party, party_id)
    if party is None:
        raise NotFound("there's no such party")
    return party


async def team_ids(session: AsyncSession, party_id: int) -> list[int]:
    """The party's teams, in the order they joined."""
    rows = await session.scalars(select(PartyTeam.team_id).where(PartyTeam.party_id == party_id).order_by(PartyTeam.position))
    return list(rows.all())


async def hero_ids(session: AsyncSession, party_id: int) -> list[int]:
    """The party's heroes: team by team in the order the teams joined, each team by slot."""
    rows = await session.execute(
        select(TeamMember.hero_id)
        .join(PartyTeam, PartyTeam.team_id == TeamMember.team_id)
        .where(PartyTeam.party_id == party_id)
        .order_by(PartyTeam.position, TeamMember.slot)
    )
    return [hero_id for (hero_id,) in rows.all()]


async def _team(session: AsyncSession, team_id: int) -> Team:
    team = await session.get(Team, team_id)
    if team is None:
        raise NotFound("there's no such team")
    return team


async def _next_position(session: AsyncSession, party_id: int) -> int:
    last = await session.scalar(select(func.max(PartyTeam.position)).where(PartyTeam.party_id == party_id))
    return 0 if last is None else last + 1


async def create_party(session: AsyncSession, team_id: int, party_size: int) -> Party:
    """A new party of one team, standing where the team's first hero stands (the hub if it has none)."""
    team = await _team(session, team_id)
    if await party_of(session, team.id) is not None:
        raise PartyError("that team is already in a party: it must leave it first")
    count = await team_size(session, team.id)
    if count > party_size:
        raise PartyError(f"a party has room for {party_size} heroes, and that team has {count}")
    first = await session.scalar(
        select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == team.id).order_by(TeamMember.slot).limit(1)
    )
    if first is not None:
        where = {"map_id": first.map_id, "x": first.x, "y": first.y}
    else:
        where = {"map_id": (await ensure_start(session)).id, "x": 0, "y": 0}
    party = Party(leader_account_id=team.account_id, **where)
    session.add(party)
    await session.flush()
    session.add(PartyTeam(party_id=party.id, team_id=team.id, position=0))
    await _flush(session)
    return party


async def play(session: AsyncSession, team_id: int, party_size: int) -> Party:
    """The team enters the game: its player selected it to play, and from then on it is in a party (of just that team, at first).
    Safe to repeat: a team that is in a party already gets that party back. A team with no heroes can't play."""
    team = await _team(session, team_id)
    party = await party_of(session, team.id)
    if party is not None:
        return party
    if await team_size(session, team.id) == 0:
        raise PartyError("a team needs a hero to play")
    return await create_party(session, team.id, party_size)


async def _check_whole(session: AsyncSession, party_id: int) -> None:
    """A party in a town (suspended, its teams apart) can't change until it is whole again."""
    if await session.scalar(select(TownVisit.id).where(TownVisit.party_id == party_id)) is not None:
        raise PartyError("that party is in a town: it can change when its teams are ready to leave and it is whole again")


async def _flush(session: AsyncSession) -> None:
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as error:
        raise PartyError("that team is already in a party") from error


async def _accept(session: AsyncSession, party: Party, accepted_by: int | None) -> None:
    """The player who accepted a team or party into this one leads it from now on (they must have a team in it)."""
    if accepted_by is None:
        return
    owns = select(PartyTeam.id).join(Team, Team.id == PartyTeam.team_id).where(PartyTeam.party_id == party.id, Team.account_id == accepted_by)
    if await session.scalar(owns.limit(1)) is None:
        raise PartyError("only a player with a team in the party can accept another party into it")
    party.leader_account_id = accepted_by


async def join_party(session: AsyncSession, party_id: int, team_id: int, party_size: int, accepted_by: int | None = None, apart_ok: bool = False) -> Party:
    """The team joins the party whole, if there is a place for every one of its heroes. $accepted_by, the account of the
    player who accepted it, becomes the party's leader. A party that is apart in a town refuses, unless $apart_ok (the guild
    adds teams in town): the team then joins the town visit as a group of its own, not waiting, so the party can leave only once it
    is ready too."""
    party = await get_party(session, party_id)
    visit = await session.scalar(select(TownVisit).where(TownVisit.party_id == party.id))
    if visit is None or not apart_ok:
        await _check_whole(session, party.id)
    team = await _team(session, team_id)
    if await party_of(session, team.id) is not None:
        raise PartyError("that team is already in a party: it must leave it first")
    have, coming = await size(session, party.id), await team_size(session, team.id)
    if have + coming > party_size:
        raise PartyError(f"a party has room for {party_size} heroes: it has {have} and that team has {coming}")
    await _accept(session, party, accepted_by)
    session.add(PartyTeam(party_id=party.id, team_id=team.id, position=await _next_position(session, party.id)))
    await _flush(session)
    if visit is not None:
        last = await session.scalar(select(func.max(TownTeam.group)).where(TownTeam.visit_id == visit.id))
        session.add(TownTeam(visit_id=visit.id, team_id=team.id, group=0 if last is None else last + 1, waiting=False))
        visit.formation = [*visit.formation, team.id]
        await session.flush()
    return party


async def leave_party(session: AsyncSession, team_id: int) -> int | None:
    """The whole team leaves its party. An empty party goes with it. Returns the party's id (None: it wasn't in one)."""
    row = await session.scalar(select(PartyTeam).where(PartyTeam.team_id == team_id))
    if row is None:
        return None
    party_id = row.party_id
    if await session.scalar(select(TownTeam.id).where(TownTeam.team_id == team_id)) is not None:
        raise PartyError("that team is in a town with its party: it leaves with the town's own call")
    await session.delete(row)
    await session.execute(delete(StandingStatus).where(StandingStatus.target_kind == TEAM, StandingStatus.target_id == team_id, StandingStatus.ends_party.is_(True)))  # a guest pass is for this party
    await session.flush()
    if not await session.scalar(select(func.count()).select_from(PartyTeam).where(PartyTeam.party_id == party_id)):
        visits = select(TownVisit.id).where(TownVisit.party_id == party_id)  # (a party apart in a town goes with its visit)
        await session.execute(delete(TownTeam).where(TownTeam.visit_id.in_(visits)))
        await session.execute(delete(TownVisit).where(TownVisit.party_id == party_id))
        await session.execute(delete(PartyRequest).where(PartyRequest.party_id == party_id))
        await session.execute(delete(JoinOffer).where(JoinOffer.party_id == party_id))
        await session.execute(delete(StandingStatus).where(StandingStatus.target_kind == PARTY, StandingStatus.target_id == party_id))
        await session.execute(delete(Party).where(Party.id == party_id))
    else:
        await leader_account(session, party_id)  # (settles the leadership if the leaver was the last of the leader's teams)
    return party_id


async def merge_parties(session: AsyncSession, keep_id: int, absorb_id: int, party_size: int, accepted_by: int | None = None) -> Party:
    """Every team of the second party joins the first, in the order they were in; the second party is gone.
    Both must fit together: parties never merge in part. $accepted_by, the account of the player who accepted the
    second party into theirs, becomes the leader (else the first party keeps its own)."""
    if keep_id == absorb_id:
        raise PartyError("a party can't merge with itself")
    keep, absorb = await get_party(session, keep_id), await get_party(session, absorb_id)
    await _check_whole(session, keep.id)
    await _check_whole(session, absorb.id)
    have, coming = await size(session, keep.id), await size(session, absorb.id)
    if have + coming > party_size:
        raise PartyError(f"a party has room for {party_size} heroes: they have {have} and {coming} between them")
    await _accept(session, keep, accepted_by)
    position = await _next_position(session, keep.id)
    moving = (await session.scalars(select(PartyTeam).where(PartyTeam.party_id == absorb.id).order_by(PartyTeam.position))).all()
    # Moved in one go after taking the first party's end positions, so no two ever clash.
    for offset, row in enumerate(moving):
        row.party_id, row.position = keep.id, position + offset
    await session.flush()
    await session.execute(delete(PartyRequest).where(PartyRequest.party_id == absorb.id))
    await session.execute(delete(JoinOffer).where(JoinOffer.party_id == absorb.id))
    await session.execute(delete(StandingStatus).where(StandingStatus.target_kind == PARTY, StandingStatus.target_id == absorb.id))
    await session.execute(delete(Party).where(Party.id == absorb.id))
    return keep


async def leader_account(session: AsyncSession, party_id: int) -> int | None:
    """The account of the party's leader. If the stored leader has no team in the party any more (it left, or the party
    has none recorded), the owner of the party's first team leads, and that is stored."""
    party = await get_party(session, party_id)
    owners = (await session.scalars(
        select(Team.account_id).join(PartyTeam, PartyTeam.team_id == Team.id).where(PartyTeam.party_id == party.id).order_by(PartyTeam.position)
    )).all()
    if party.leader_account_id not in owners:
        party.leader_account_id = owners[0] if owners else None
        await session.flush()
    return party.leader_account_id


async def is_leader(session: AsyncSession, hero: Hero) -> bool:
    """Whether the hero's player leads the party the hero's team is in. A team in no party stands alone, led by its owner."""
    team = await session.scalar(select(Team).join(TeamMember, TeamMember.team_id == Team.id).where(TeamMember.hero_id == hero.id))
    if team is None:
        return False
    party = await party_of(session, team.id)
    if party is None:
        return team.account_id == hero.account_id
    return await leader_account(session, party.id) == hero.account_id


async def check_room(session: AsyncSession, team_id: int, party_size: int) -> None:
    """For a hero about to join a team: if the team is in a party, the party needs a place for them."""
    party = await party_of(session, team_id)
    if party is not None and await size(session, party.id) + 1 > party_size:
        raise PartyError(f"that team's party has room for {party_size} heroes and is full")


async def view(session: AsyncSession, party_id: int) -> dict:
    party = await get_party(session, party_id)
    teams = await team_ids(session, party.id)
    return {"id": party.id, "map_id": party.map_id, "x": party.x, "y": party.y, "teams": teams, "heroes": await size(session, party.id)}
