"""Profiles: what each page shows, and who may change it.

The owner's calls (``profiles.routes``) use the first half; the public pages are built by the second half from a token and
show only what the owner chose (a team's page and link exist only while its player has made it visible; its name is on its alliance's page
either way; the player's handle is in the roster only for a visible team whose player is in the directory): never an account id, a username or an email. Tokens are random and unguessable, and a
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
    each can be shown or hidden (hidden until the player shows it)."""
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


async def set_directory(session: AsyncSession, account: Account, enabled: bool) -> PlayerProfile:
    """Opts the player in or out of the directory (out by default): in, their handle appears in the rosters of their teams'
    alliances and alliance members can go from a team's page to the player's page."""
    profile = await player(session, account) or await publish(session, account)
    profile.directory = enabled
    await session.flush()
    return profile


async def _own_team(session: AsyncSession, account: Account, team_id: int) -> Team:
    team = await session.scalar(select(Team).where(Team.id == team_id, Team.account_id == account.id))
    if team is None:
        raise NotFound("there's no such team")
    return team


async def set_visible(session: AsyncSession, account: Account, team_id: int, visible: bool) -> TeamProfile:
    """Shows or hides one team: a visible team's page is reachable from the player's page and the alliances' pages."""
    team = await _own_team(session, account, team_id)
    if await player(session, account) is None:
        await publish(session, account)
    await _ensure_teams(session, account)
    row = (await _team_rows(session, [team]))[team.id]
    row.visible = visible
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
        "directory": bool(profile and profile.directory),
        "teams": [
            {"id": team.id, "name": team.name, "visible": rows[team.id].visible if team.id in rows else False,
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
    """What anyone with the link sees: the handle, the bio and the player's visible teams, each with the token of its page."""
    profile = await session.scalar(select(PlayerProfile).where(PlayerProfile.token == token))
    if profile is None:
        raise NotFound("there's no such page")
    account = await session.get(Account, profile.account_id)
    shown = (await session.execute(
        select(Team, TeamProfile).join(TeamProfile, TeamProfile.team_id == Team.id)
        .where(Team.account_id == profile.account_id, TeamProfile.visible.is_(True)).order_by(Team.id)
    )).all()
    return {"handle": account.handle, "bio": profile.bio, "teams": [{"name": team.name, "page": row.token} for team, row in shown]}


async def _team_page(session: AsyncSession, team: Team, members: bool) -> dict:
    """A visible team's page: its name and the alliances it is in (each with a link once it has a page). The player's
    handle is on it only if the player is in the directory, and an alliance's $members (reading it as one) also get the
    token of the player's page (``player``)."""
    owner = await session.get(Account, team.account_id)
    profile = await session.scalar(select(PlayerProfile).where(PlayerProfile.account_id == team.account_id))
    rows = (await session.execute(
        select(Alliance, AllianceProfile).join(AllianceMember, AllianceMember.alliance_id == Alliance.id)
        .outerjoin(AllianceProfile, AllianceProfile.alliance_id == Alliance.id)
        .where(AllianceMember.team_id == team.id).order_by(Alliance.name)
    )).all()
    page = {"name": team.name}
    if profile is not None and profile.directory:
        page["handle"] = owner.handle
        if members:
            page["player"] = profile.token
    page["alliances"] = [{"name": alliance.name, "page": row.token if row else None} for alliance, row in rows]
    return page


async def _visible_team(session: AsyncSession, team_id: int) -> Team | None:
    """The team, if its player made it visible."""
    row = await session.scalar(select(TeamProfile).where(TeamProfile.team_id == team_id, TeamProfile.visible.is_(True)))
    return await session.get(Team, team_id) if row else None


async def team_page(session: AsyncSession, token: str) -> dict:
    """The page at a team's token; it is there only while the team is visible."""
    row = await session.scalar(select(TeamProfile).where(TeamProfile.token == token))
    team = await _visible_team(session, row.team_id) if row else None
    if team is None:
        raise NotFound("there's no such page")
    return await _team_page(session, team, False)


async def alliance_page(session: AsyncSession, token: str) -> dict:
    """An alliance's page: its description and every team in it, by name and role, so anyone can see which teams belong. A
    team has a link to its page, and its player's handle if the player is in the directory, only while it is visible; a
    hidden team shows its name alone (``page`` is None), never its members."""
    row = await session.scalar(select(AllianceProfile).where(AllianceProfile.token == token))
    if row is None:
        raise NotFound("there's no such page")
    alliance = await session.get(Alliance, row.alliance_id)
    found = (await session.execute(
        select(Team, AllianceMember, TeamProfile, PlayerProfile, Account)
        .join(AllianceMember, AllianceMember.team_id == Team.id)
        .join(Account, Account.id == Team.account_id)
        .outerjoin(TeamProfile, TeamProfile.team_id == Team.id)
        .outerjoin(PlayerProfile, PlayerProfile.account_id == Team.account_id)
        .where(AllianceMember.alliance_id == alliance.id).order_by(AllianceMember.id)
    )).all()
    teams = []
    for team, member, team_row, player_row, owner in found:
        shown = team_row is not None and team_row.visible
        entry = {"name": team.name, "role": member.role, "page": team_row.token if shown else None}
        if shown and player_row is not None and player_row.directory:
            entry["handle"] = owner.handle
        teams.append(entry)
    return {"name": alliance.name, "bio": row.bio, "teams": teams}


async def member_team_page(session: AsyncSession, account: Account, alliance_id: int, team_id: int) -> dict:
    """A visible team's page for a member of its alliance: like the public one, plus the token of the player's page when the
    player is in the directory. A hidden team is not there, to its alliance either."""
    alliance = await alliances.get_alliance(session, alliance_id)
    await alliances.reading_team(session, account, alliance)  # NotFound unless one of the account's teams is a member
    if await alliances.member_row(session, alliance.id, team_id) is None:
        raise NotFound("there's no such team in this alliance")
    team = await _visible_team(session, team_id)
    if team is None:
        raise NotFound("there's no such team in this alliance")
    return await _team_page(session, team, True)
