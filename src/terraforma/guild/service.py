"""The adventurers guild: bringing teams together into parties, from a conversation with the guild's NPC.

Three things, each an activity a dialog reaches with its own tag (``add_team``, ``find_party``, ``party_requests``; the guild's
price, if any, is whatever the dialog charges before the tag): a player adds another of their own teams to the party they
are in; a team asks to join an open party of allies (``Guild.may_ask``); and a party's leader answers the requests. A party is
opened to requests by the dialog's ``open_party`` tag. Joining works while the party is apart in a town: the team becomes a
group of the visit, not waiting, and the party leaves once it is ready too. These are service functions taking ids; the
routes check who may call them.
"""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Hero, Team, TeamMember
from ..npcs.state import DialogState
from ..parties import service as parties
from ..parties.models import Party, PartyRequest, PartyTeam
from ..profiles.models import TeamProfile
from ..towns import service as towns
from ..towns.models import TownTeam
from .hooks import Guild


class GuildError(ValueError):
    """Something the caller can fix: the message says what."""


async def _party_id(session: AsyncSession, hero: Hero) -> int:
    """The real party the hero's team is in (its formation survives a town visit)."""
    party = await DialogState(session, hero).party()
    if party is None:
        raise GuildError("that hero's team has not entered the game")
    return party.party_id


async def _leave(session: AsyncSession, team_id: int) -> None:
    """The team leaves its party (and the town visit it is in)."""
    if await session.scalar(select(TownTeam.id).where(TownTeam.team_id == team_id)) is not None:
        await towns.leave_party(session, team_id)
    else:
        await parties.leave_party(session, team_id)


async def _join(session: AsyncSession, party_id: int, team_id: int, party_size: int, accepted_by: int | None) -> None:
    try:
        await parties.join_party(session, party_id, team_id, party_size, accepted_by=accepted_by, apart_ok=True)
    except (parties.PartyError, towns.TownError) as error:
        raise GuildError(str(error)) from error


async def set_open(session: AsyncSession, party_id: int, value: bool) -> None:
    await session.execute(update(Party).where(Party.id == party_id).values(open=value))


async def teams_to_add(session: AsyncSession, hero: Hero) -> list[dict]:
    """The player's own teams that could be added to the hero's party: not in it, with heroes, and in no party or in a party of just
    themselves."""
    party_id = await _party_id(session, hero)
    out = []
    for team in (await session.scalars(select(Team).where(Team.account_id == hero.account_id).order_by(Team.id))).all():
        if await parties.team_size(session, team.id) == 0:
            continue
        theirs = await parties.party_of(session, team.id)
        if theirs is None or (theirs.id != party_id and len(await parties.team_ids(session, theirs.id)) == 1):
            out.append({"id": team.id, "name": team.name})
    return out


async def add_team(session: AsyncSession, hero: Hero, team_id: int, party_size: int) -> dict:
    """The hero's player adds one of their own teams to the hero's party, and its heroes stand where the hero does. A team in a
    party of just itself leaves it first. All or nothing."""
    party_id = await _party_id(session, hero)
    if team_id not in {team["id"] for team in await teams_to_add(session, hero)}:
        raise GuildError("that team can't be added to this party")
    async with session.begin_nested():
        await _leave(session, team_id)
        await _join(session, party_id, team_id, party_size, None)
        heroes = select(TeamMember.hero_id).where(TeamMember.team_id == team_id)
        await session.execute(update(Hero).where(Hero.id.in_(heroes)).values(map_id=hero.map_id, x=hero.x, y=hero.y))
    return {"party": party_id, "team": team_id}


async def open_parties(session: AsyncSession, hero: Hero) -> list[dict]:
    """The open parties on the hero's map, other than their own: each with its places left and its teams' names (a hidden
    team shows no name)."""
    own = await _party_id(session, hero)
    rows = (await session.scalars(select(Party).where(Party.open.is_(True), Party.map_id == hero.map_id, Party.id != own).order_by(Party.id))).all()
    shown = []
    for party in rows:
        teams = []
        for team_id in await parties.team_ids(session, party.id):
            visible = await session.scalar(select(TeamProfile.visible).where(TeamProfile.team_id == team_id))
            teams.append({"name": (await session.get(Team, team_id)).name if visible else None})
        shown.append({"id": party.id, "heroes": await parties.size(session, party.id), "teams": teams})
    return shown


async def ask(session: AsyncSession, guild: Guild, hero: Hero, party_id: int, party_size: int) -> dict:
    """The hero's team asks to join an open party. Refused unless the party is open, is another party, is an ally the game's
    ``Guild.may_ask`` allows, has room for the whole team and was not asked already."""
    team_id = await DialogState(session, hero).team_id()
    own = await _party_id(session, hero)
    party = await session.get(Party, party_id)
    if party is None or not party.open or party.id == own:
        raise GuildError("that party is not looking for members")
    if reason := await guild.may_ask(session, team_id, party.id):
        raise GuildError(reason)
    have, coming = await parties.size(session, party.id), await parties.team_size(session, team_id)
    if have + coming > party_size:
        raise GuildError("that party has no room for the whole team")
    if await session.scalar(select(PartyRequest.id).where(PartyRequest.party_id == party.id, PartyRequest.team_id == team_id)) is not None:
        raise GuildError("that party was asked already")
    session.add(PartyRequest(party_id=party.id, team_id=team_id))
    await session.flush()
    return {"party": party.id, "team": team_id}


async def requests(session: AsyncSession, hero: Hero) -> list[dict]:
    """The teams asking to join the hero's party, oldest first. Only the party's leader sees them."""
    party_id = await _party_id(session, hero)
    if await parties.leader_account(session, party_id) != hero.account_id:
        raise GuildError("only the party's leader answers requests")
    rows = (await session.scalars(select(PartyRequest).where(PartyRequest.party_id == party_id).order_by(PartyRequest.id))).all()
    return [{"id": row.id, "team": (await session.get(Team, row.team_id)).name, "heroes": await parties.team_size(session, row.team_id)} for row in rows]


async def answer(session: AsyncSession, hero: Hero, request_id: int, accept: bool, party_size: int) -> dict:
    """The leader accepts or declines a request. Accepting brings the team into the party (it leaves its own), and the leader,
    who accepted it, leads the party."""
    party_id = await _party_id(session, hero)
    if await parties.leader_account(session, party_id) != hero.account_id:
        raise GuildError("only the party's leader answers requests")
    row = await session.scalar(select(PartyRequest).where(PartyRequest.id == request_id, PartyRequest.party_id == party_id))
    if row is None:
        raise GuildError("there is no such request")
    team_id = row.team_id
    if accept:
        async with session.begin_nested():
            if await session.scalar(select(PartyTeam.id).where(PartyTeam.team_id == team_id)) is not None:
                await _leave(session, team_id)
            await _join(session, party_id, team_id, party_size, hero.account_id)
            await session.execute(delete(PartyRequest).where(PartyRequest.team_id == team_id))
    else:
        await session.delete(row)
        await session.flush()
    return {"accepted": accept, "team": team_id}
