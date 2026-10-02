"""Founding, joining, running and leaving alliances, through the game's ``Alliances``.

Service functions: the calls (``alliances.routes``) use them, and so can a game's own rules. They take the team that is
acting (a team's owner acts for it, and the calls check that), raise ``AllianceError`` (a message the player can read),
and leave the rolling back to the caller's transaction. What a role may do is the game's decision (``Alliances.allowed``).
"""

import re

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..heroes.models import Team
from ..models import Account
from ..relations import service as relations
from ..relations.hooks import Ref
from .hooks import Alliances
from .models import NAME_MAX, Alliance, AllianceInvite, AllianceMember


class AllianceError(ValueError):
    """Something the player can fix: the message says what."""


class NotFound(AllianceError):
    pass


class Forbidden(AllianceError):
    """The team's role in the alliance, or the game's rules, do not let it."""


def name_key(name: str) -> str:
    return " ".join(name.split()).casefold()


def check_name(name: str) -> str:
    name = " ".join(name.split())
    if not 1 <= len(name) <= NAME_MAX or not name.isprintable():
        raise AllianceError(f"an alliance's name is 1 to {NAME_MAX} characters")
    if re.fullmatch(r"[\W_]+", name):
        raise AllianceError("an alliance's name needs a letter or a digit")
    return name


# --- finding things ------------------------------------------------------------------------------------------------

async def get_alliance(session: AsyncSession, alliance_id: int) -> Alliance:
    alliance = await session.get(Alliance, alliance_id)
    if alliance is None:
        raise NotFound("there's no such alliance")
    return alliance


async def member_row(session: AsyncSession, alliance_id: int, team_id: int) -> AllianceMember | None:
    return await session.scalar(select(AllianceMember).where(AllianceMember.alliance_id == alliance_id, AllianceMember.team_id == team_id))


async def members(session: AsyncSession, alliance_id: int) -> list[AllianceMember]:
    """The alliance's teams in the order they joined."""
    return list((await session.scalars(select(AllianceMember).where(AllianceMember.alliance_id == alliance_id).order_by(AllianceMember.id))).all())


async def _count(session: AsyncSession, table, **where) -> int:
    query = select(func.count()).select_from(table)
    for column, value in where.items():
        query = query.where(getattr(table, column) == value)
    return await session.scalar(query)


async def _team(session: AsyncSession, team_id: int) -> Team:
    team = await session.get(Team, team_id)
    if team is None:
        raise NotFound("there's no such team")
    return team


async def _acting(session, hooks: Alliances, alliance: Alliance, actor_team_id: int, action: str, target_team_id=None, role=None):
    """The acting member and the member acted on, if the game's rules let the actor do ``action``."""
    actor = await member_row(session, alliance.id, actor_team_id)
    if actor is None:
        raise Forbidden("that team is not in the alliance")
    target = None
    if target_team_id is not None:
        target = await member_row(session, alliance.id, target_team_id)
        if target is None:
            raise NotFound("that team is not in the alliance")
    if not await hooks.allowed(session, alliance, actor, action, target, role):
        raise Forbidden("your role can't do that")
    return actor, target


# --- founding, inviting, joining --------------------------------------------------------------------------------------------

async def found(session: AsyncSession, hooks: Alliances, team: Team, name: str) -> Alliance:
    """The team founds an alliance and holds its founder role."""
    name = check_name(name)
    if not await hooks.may_found(session, team):
        raise Forbidden("this team can't found an alliance")
    if await _count(session, AllianceMember, team_id=team.id) >= hooks.max_per_team:
        raise AllianceError(f"a team can be in {hooks.max_per_team} alliances")
    alliance = Alliance(name=name, name_key=name_key(name))
    try:
        async with session.begin_nested():  # (added inside, so a refused name leaves nothing pending in the session)
            session.add(alliance)
            await session.flush()
    except IntegrityError as error:
        raise AllianceError("an alliance already has that name") from error
    session.add(AllianceMember(alliance_id=alliance.id, team_id=team.id, role=hooks.founder_role))
    await session.flush()
    return alliance


async def invite(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, team_id: int) -> AllianceInvite:
    await _acting(session, hooks, alliance, actor_team_id, "invite")
    team = await _team(session, team_id)
    if await member_row(session, alliance.id, team.id) is not None:
        raise AllianceError("that team is already in the alliance")
    if await session.scalar(select(AllianceInvite).where(AllianceInvite.alliance_id == alliance.id, AllianceInvite.team_id == team.id)):
        raise AllianceError("that team has been invited already")
    held = await _count(session, AllianceMember, alliance_id=alliance.id) + await _count(session, AllianceInvite, alliance_id=alliance.id)
    if held >= hooks.max_members:
        raise AllianceError(f"an alliance holds {hooks.max_members} teams, counting those invited")
    row = AllianceInvite(alliance_id=alliance.id, team_id=team.id, invited_by_team_id=actor_team_id)
    session.add(row)
    await session.flush()
    return row


async def withdraw(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, team_id: int) -> None:
    await _acting(session, hooks, alliance, actor_team_id, "withdraw")
    gone = await session.execute(delete(AllianceInvite).where(AllianceInvite.alliance_id == alliance.id, AllianceInvite.team_id == team_id))
    if gone.rowcount == 0:
        raise NotFound("that team has not been invited")


async def accept(session: AsyncSession, hooks: Alliances, team: Team, alliance_id: int) -> Alliance:
    """The invited team joins, in the default role."""
    invitation = await session.scalar(select(AllianceInvite).where(AllianceInvite.alliance_id == alliance_id, AllianceInvite.team_id == team.id))
    if invitation is None:
        raise NotFound("there's no such invitation")
    alliance = await get_alliance(session, alliance_id)
    if not await hooks.may_join(session, alliance, team):
        raise Forbidden("this team can't join that alliance")
    if await _count(session, AllianceMember, team_id=team.id) >= hooks.max_per_team:
        raise AllianceError(f"a team can be in {hooks.max_per_team} alliances")
    await session.delete(invitation)
    session.add(AllianceMember(alliance_id=alliance.id, team_id=team.id, role=hooks.default_role))
    await session.flush()
    return alliance


async def decline(session: AsyncSession, team: Team, alliance_id: int) -> None:
    gone = await session.execute(delete(AllianceInvite).where(AllianceInvite.alliance_id == alliance_id, AllianceInvite.team_id == team.id))
    if gone.rowcount == 0:
        raise NotFound("there's no such invitation")


# --- running it ------------------------------------------------------------------------------------------------------------------

async def _delete_alliance(session: AsyncSession, alliance: Alliance) -> None:
    await session.execute(delete(AllianceMember).where(AllianceMember.alliance_id == alliance.id))
    await session.execute(delete(AllianceInvite).where(AllianceInvite.alliance_id == alliance.id))
    await relations.forget(session, Ref("alliance", alliance.id))
    await session.delete(alliance)
    await session.flush()


async def leave(session: AsyncSession, hooks: Alliances, alliance: Alliance, team_id: int) -> bool:
    """The team leaves. The last team out takes the alliance with it (True); the only team holding the founder role
    can't leave while others remain: it hands the role over first."""
    row = await member_row(session, alliance.id, team_id)
    if row is None:
        raise NotFound("that team is not in the alliance")
    others = [other for other in await members(session, alliance.id) if other.team_id != team_id]
    if not others:
        await _delete_alliance(session, alliance)
        return True
    if row.role == hooks.founder_role and not any(other.role == hooks.founder_role for other in others):
        raise AllianceError("hand the leadership over before leaving")
    await session.delete(row)
    await session.flush()
    return False


async def remove(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> None:
    _, target = await _acting(session, hooks, alliance, actor_team_id, "remove", target_team_id)
    await session.delete(target)
    await session.flush()


async def set_role(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int, role: str) -> AllianceMember:
    _, target = await _acting(session, hooks, alliance, actor_team_id, "set_role", target_team_id, role)
    target.role = role
    await session.flush()
    return target


async def hand_over(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> None:
    """The acting team gives the founder role to another team and drops to the next role down."""
    actor, target = await _acting(session, hooks, alliance, actor_team_id, "hand_over", target_team_id)
    target.role = hooks.founder_role
    below = hooks.roles.index(hooks.founder_role) + 1
    actor.role = hooks.roles[below] if below < len(hooks.roles) else hooks.default_role
    await session.flush()


async def disband(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int) -> None:
    await _acting(session, hooks, alliance, actor_team_id, "disband")
    await _delete_alliance(session, alliance)


async def remove_team(session: AsyncSession, hooks: Alliances, team_id: int) -> None:
    """A team is going: it leaves every alliance (an alliance left without a founder gives the role to the best-ranked,
    longest-standing team left; the last team out takes the alliance) and its invitations go."""
    await session.execute(delete(AllianceInvite).where(AllianceInvite.team_id == team_id))
    for row in list((await session.scalars(select(AllianceMember).where(AllianceMember.team_id == team_id))).all()):
        alliance = await session.get(Alliance, row.alliance_id)
        others = [other for other in await members(session, alliance.id) if other.team_id != team_id]
        if not others:
            await _delete_alliance(session, alliance)
            continue
        if row.role == hooks.founder_role and not any(other.role == hooks.founder_role for other in others):
            heir = min(others, key=lambda other: (hooks.rank(other.role), other.id))
            heir.role = hooks.founder_role
        await session.delete(row)
    await session.flush()


# --- what the player sees --------------------------------------------------------------------------------------------------------------

async def view(session: AsyncSession, hooks: Alliances, alliance: Alliance, viewer_team_id: int) -> dict:
    """The alliance as one of its members sees it: the roles, the teams, and (if its role may invite) who is invited."""
    rows = await members(session, alliance.id)
    names = {team.id: team.name for team in (await session.scalars(select(Team).where(Team.id.in_([row.team_id for row in rows])))).all()}
    me = next(row for row in rows if row.team_id == viewer_team_id)
    shown = {
        "id": alliance.id, "name": alliance.name, "roles": list(hooks.roles), "you": {"team_id": me.team_id, "role": me.role},
        "can": sorted(hooks.permissions.get(me.role, ())),
        "members": [{"team_id": row.team_id, "team": names.get(row.team_id), "role": row.role} for row in rows],
    }
    if "invite" in hooks.permissions.get(me.role, ()) or "withdraw" in hooks.permissions.get(me.role, ()):
        pending = (await session.scalars(select(AllianceInvite).where(AllianceInvite.alliance_id == alliance.id).order_by(AllianceInvite.id))).all()
        invited = {team.id: team.name for team in (await session.scalars(select(Team).where(Team.id.in_([row.team_id for row in pending])))).all()}
        shown["invited"] = [{"team_id": row.team_id, "team": invited.get(row.team_id)} for row in pending]
    return shown


async def mine(session: AsyncSession, account: Account) -> dict:
    """The account's teams' alliances, and the invitations waiting for its teams."""
    teams = {team.id: team.name for team in (await session.scalars(select(Team).where(Team.account_id == account.id))).all()}
    rows = (await session.execute(
        select(AllianceMember, Alliance).join(Alliance, Alliance.id == AllianceMember.alliance_id)
        .where(AllianceMember.team_id.in_(list(teams))).order_by(Alliance.name, AllianceMember.id)
    )).all()
    invites = (await session.execute(
        select(AllianceInvite, Alliance).join(Alliance, Alliance.id == AllianceInvite.alliance_id)
        .where(AllianceInvite.team_id.in_(list(teams))).order_by(AllianceInvite.id)
    )).all()
    return {
        "alliances": [{"id": alliance.id, "name": alliance.name, "team_id": row.team_id, "team": teams[row.team_id], "role": row.role} for row, alliance in rows],
        "invitations": [{"alliance_id": alliance.id, "alliance": alliance.name, "team_id": row.team_id, "team": teams[row.team_id]} for row, alliance in invites],
    }


async def speaking_team(session: AsyncSession, hooks: Alliances, account: Account, alliance: Alliance, action: str = "speak") -> AllianceMember:
    """One of the account's teams in the alliance whose role may do ``action``; Forbidden if there is none, and NotFound
    if the account has no team in the alliance at all (so an outsider cannot tell it exists)."""
    owned = [row for row in await members(session, alliance.id) if (await session.get(Team, row.team_id)).account_id == account.id]
    if not owned:
        raise NotFound("there's no such alliance")
    for row in sorted(owned, key=lambda each: hooks.rank(each.role)):
        if await hooks.allowed(session, alliance, row, action):
            return row
    raise Forbidden("your role can't do that")


async def reading_team(session: AsyncSession, account: Account, alliance: Alliance) -> AllianceMember:
    """One of the account's teams in the alliance (any role), or NotFound."""
    for row in await members(session, alliance.id):
        if (await session.get(Team, row.team_id)).account_id == account.id:
            return row
    raise NotFound("there's no such alliance")
