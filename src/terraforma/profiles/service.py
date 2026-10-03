"""Profiles: what each page shows, and who may change it.

The owner's calls (``profiles.routes``) use the first half; the public pages are built by the second half from a token and
show only what the owner chose: never an account id, a username or an email. Tokens are random and unguessable, and a
new one replaces the old for good."""

import secrets

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alliances import service as alliances
from ..alliances.models import Alliance, AllianceMember
from ..heroes.models import Team
from ..models import Account
from .models import BIO_MAX, AllianceProfile, PlayerProfile, TeamProfile


class ProfileError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(ProfileError):
    pass


def new_token() -> str:
    return secrets.token_urlsafe(16)  # 22 characters


def clean_bio(text: str) -> str:
    """The bio with its edges trimmed; at most ``BIO_MAX`` characters, and no control characters."""
    text = text.strip()
    if len(text) > BIO_MAX:
        raise ProfileError(f"keep it to {BIO_MAX} characters")
    if any(ord(char) < 32 and char not in "\n\t" for char in text):
        raise ProfileError("the bio can't hold control characters")
    return text


# --- the owner's side -------------------------------------------------------------------------------------------------------------

async def player(session: AsyncSession, account: Account) -> PlayerProfile | None:
    return await session.scalar(select(PlayerProfile).where(PlayerProfile.account_id == account.id))


async def _teams(session: AsyncSession, account: Account) -> list[Team]:
    return list((await session.scalars(select(Team).where(Team.account_id == account.id).order_by(Team.id))).all())


async def _team_rows(session: AsyncSession, teams: list[Team]) -> dict[int, TeamProfile]:
    rows = (await session.scalars(select(TeamProfile).where(TeamProfile.team_id.in_([team.id for team in teams])))).all()
    return {row.team_id: row for row in rows}


async def publish(session: AsyncSession, account: Account) -> PlayerProfile:
    """Makes the player's page (or gives it a new address: the old one stops working at once). Every team gets a row, so
    each can be listed or hidden."""
    profile = await player(session, account)
    if profile is None:
        profile = PlayerProfile(account_id=account.id, token=new_token())
        session.add(profile)
    else:
        profile.token = new_token()
    await _ensure_teams(session, account)
    await session.flush()
    return profile


async def _ensure_teams(session: AsyncSession, account: Account) -> None:
    teams = await _teams(session, account)
    have = await _team_rows(session, teams)
    for team in teams:
        if team.id not in have:
            session.add(TeamProfile(team_id=team.id, token=new_token()))
    await session.flush()


async def set_bio(session: AsyncSession, account: Account, bio: str) -> PlayerProfile:
    text = clean_bio(bio)
    profile = await player(session, account)
    if profile is None:
        profile = await publish(session, account)
    profile.bio = text
    await session.flush()
    return profile


async def set_team_pages(session: AsyncSession, account: Account, enabled: bool) -> PlayerProfile:
    profile = await player(session, account) or await publish(session, account)
    profile.team_pages = enabled
    await _ensure_teams(session, account)
    await session.flush()
    return profile


async def set_team_alliances(session: AsyncSession, account: Account, enabled: bool) -> PlayerProfile:
    profile = await player(session, account) or await publish(session, account)
    profile.team_alliances = enabled
    await session.flush()
    return profile


async def _own_team(session: AsyncSession, account: Account, team_id: int) -> Team:
    team = await session.scalar(select(Team).where(Team.id == team_id, Team.account_id == account.id))
    if team is None:
        raise NotFound("there's no such team")
    return team


async def set_listed(session: AsyncSession, account: Account, team_id: int, listed: bool) -> TeamProfile:
    team = await _own_team(session, account, team_id)
    if await player(session, account) is None:
        await publish(session, account)
    await _ensure_teams(session, account)
    row = (await _team_rows(session, [team]))[team.id]
    row.listed = listed
    await session.flush()
    return row


async def renew_team(session: AsyncSession, account: Account, team_id: int) -> TeamProfile:
    """A new address for the team's page."""
    team = await _own_team(session, account, team_id)
    await _ensure_teams(session, account)
    row = (await _team_rows(session, [team]))[team.id]
    row.token = new_token()
    await session.flush()
    return row


async def mine(session: AsyncSession, account: Account) -> dict:
    """The owner's view: what is public, and the tokens that make up the addresses (``None`` until the page is made)."""
    profile = await player(session, account)
    teams = await _teams(session, account)
    rows = await _team_rows(session, teams)
    return {
        "token": profile.token if profile else None,
        "bio": profile.bio if profile else "",
        "handle": account.handle,
        "team_pages": bool(profile and profile.team_pages),
        "team_alliances": bool(profile and profile.team_alliances),
        "teams": [
            {"id": team.id, "name": team.name, "listed": rows[team.id].listed if team.id in rows else True,
             "token": rows[team.id].token if team.id in rows else None}
            for team in teams
        ],
    }


async def _alliance_row(session: AsyncSession, alliance: Alliance) -> AllianceProfile | None:
    return await session.scalar(select(AllianceProfile).where(AllianceProfile.alliance_id == alliance.id))


async def alliance_mine(session: AsyncSession, alliance: Alliance) -> dict:
    row = await _alliance_row(session, alliance)
    return {"alliance_id": alliance.id, "name": alliance.name, "token": row.token if row else None, "bio": row.bio if row else ""}


async def alliance_publish(session: AsyncSession, alliance: Alliance) -> AllianceProfile:
    row = await _alliance_row(session, alliance)
    if row is None:
        row = AllianceProfile(alliance_id=alliance.id, token=new_token())
        session.add(row)
    else:
        row.token = new_token()
    await session.flush()
    return row


async def alliance_set_bio(session: AsyncSession, alliance: Alliance, bio: str) -> AllianceProfile:
    text = clean_bio(bio)
    row = await _alliance_row(session, alliance) or await alliance_publish(session, alliance)
    row.bio = text
    await session.flush()
    return row


# --- the public pages -------------------------------------------------------------------------------------------------------------

async def player_page(session: AsyncSession, token: str) -> dict:
    """What anyone with the link sees: the handle, the bio and the teams the player chose to show (each with a link only
    while the player has team pages on)."""
    profile = await session.scalar(select(PlayerProfile).where(PlayerProfile.token == token))
    if profile is None:
        raise NotFound("there's no such page")
    account = await session.get(Account, profile.account_id)
    listed = (await session.execute(
        select(Team, TeamProfile).join(TeamProfile, TeamProfile.team_id == Team.id)
        .where(Team.account_id == profile.account_id, TeamProfile.listed.is_(True)).order_by(Team.id)
    )).all()
    return {
        "handle": account.handle,
        "bio": profile.bio,
        "teams": [{"name": team.name, "page": row.token if profile.team_pages else None} for team, row in listed],
    }


async def _team_page(session: AsyncSession, team: Team, show_alliances: bool) -> dict:
    """A team's page: its name, its player's handle, and, when $show_alliances, the alliances it is in (each with a link
    once it has a page). The public page shows them only if the player opted in; the alliance's own members always see them."""
    owner = await session.get(Account, team.account_id)
    if not show_alliances:
        return {"name": team.name, "handle": owner.handle}
    rows = (await session.execute(
        select(Alliance, AllianceProfile).join(AllianceMember, AllianceMember.alliance_id == Alliance.id)
        .outerjoin(AllianceProfile, AllianceProfile.alliance_id == Alliance.id)
        .where(AllianceMember.team_id == team.id).order_by(Alliance.name)
    )).all()
    return {
        "name": team.name,
        "handle": owner.handle,
        "alliances": [{"name": alliance.name, "page": profile.token if profile else None} for alliance, profile in rows],
    }


async def team_page(session: AsyncSession, token: str) -> dict:
    """The page at a team's token; it is there only while its player has team pages on."""
    row = await session.scalar(select(TeamProfile).where(TeamProfile.token == token))
    team = await session.get(Team, row.team_id) if row else None
    profile = await session.scalar(select(PlayerProfile).where(PlayerProfile.account_id == team.account_id)) if team else None
    if team is None or profile is None or not profile.team_pages:
        raise NotFound("there's no such page")
    return await _team_page(session, team, profile.team_alliances)


async def alliance_page(session: AsyncSession, token: str) -> dict:
    """An alliance's page: its description and every team in it. A team links to its page only where its player has team
    pages on."""
    row = await session.scalar(select(AllianceProfile).where(AllianceProfile.token == token))
    if row is None:
        raise NotFound("there's no such page")
    alliance = await session.get(Alliance, row.alliance_id)
    found = (await session.execute(
        select(Team, AllianceMember, TeamProfile, PlayerProfile)
        .join(AllianceMember, AllianceMember.team_id == Team.id)
        .outerjoin(TeamProfile, TeamProfile.team_id == Team.id)
        .outerjoin(PlayerProfile, PlayerProfile.account_id == Team.account_id)
        .where(AllianceMember.alliance_id == alliance.id).order_by(AllianceMember.id)
    )).all()
    return {
        "name": alliance.name,
        "bio": row.bio,
        "teams": [
            {"name": team.name, "role": member.role,
             "page": team_row.token if team_row and player_row and player_row.team_pages else None}
            for team, member, team_row, player_row in found
        ],
    }


async def member_team_page(session: AsyncSession, account: Account, alliance_id: int, team_id: int) -> dict:
    """A team's page for a member of its alliance, whether or not the player has team pages on. There is no link to the
    player's own page (that waits on the directory, where a player opts in)."""
    alliance = await alliances.get_alliance(session, alliance_id)
    await alliances.reading_team(session, account, alliance)  # NotFound unless one of the account's teams is a member
    if await alliances.member_row(session, alliance.id, team_id) is None:
        raise NotFound("there's no such team in this alliance")
    team = await session.get(Team, team_id)
    return await _team_page(session, team, True)


