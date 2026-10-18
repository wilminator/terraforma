"""Founding, joining, running and leaving alliances, through the game's ``Alliances``.

Service functions: the calls (``alliances.routes``) use them, and so can a game's own rules. They take the team that is
acting (a team's owner acts for it, and the calls check that), raise ``AllianceError`` (a message the player can read),
and leave the rolling back to the caller's transaction. What a role may do is the game's decision (``Alliances.allowed``).
"""

import re
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..economy import NotEnoughGold
from ..fighttags.models import AllianceKept, AllianceTop
from ..heroes.models import Hero, Team, TeamMember
from ..models import Account
from ..profiles.models import AllianceProfile
from ..relations import service as relations
from ..relations.hooks import Ref
from .. import wallclock
from . import roles, treasury
from .errors import AllianceError, Forbidden, NotFound
from .hooks import ACTIONS, GOVERNED, MEMBERS_VOTE, OTHER, SUBCOMMANDER, Alliances
from .models import NAME_MAX, ROLE_MAX, Alliance, AllianceInvite, AllianceAlumnus, AllianceMember, AllianceRole, AllianceRule, Ballot, BallotVote, BallotVoter


class NeedsVote(AllianceError):
    """The alliance's rules ask for a vote before this is done: the caller puts it to a ballot (``ballots.perform``)."""

    def __init__(self, percent: int):
        super().__init__(f"that needs a vote: {percent}% of the active teams")
        self.percent = percent


#: True while a passed ballot's action runs: the vote is what the rules asked for.
_approved: ContextVar[bool] = ContextVar("alliance_action_approved", default=False)


@contextmanager
def approved():
    token = _approved.set(True)
    try:
        yield
    finally:
        _approved.reset(token)


def builtin_percent(hooks: Alliances, action: str) -> int | None:
    """The vote an action always needs, whatever the rules: the members' own actions (``hooks.MEMBERS_VOTE``)."""
    if action == "remove_commander":
        return 100
    return hooks.subcommander_percent if action in MEMBERS_VOTE else None


async def rule_percent(session: AsyncSession, alliance_id: int, action: str, role: str) -> int | None:
    """The percent the alliance asks for when a team in ``role`` does ``action``: the rule for the role, else the rule for any role, else none."""
    rows = (await session.execute(select(AllianceRule.role, AllianceRule.percent).where(AllianceRule.alliance_id == alliance_id, AllianceRule.action == action, AllianceRule.role.in_([role, ""])))).all()
    found = dict(rows)
    return found.get(role, found.get(""))


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
    if not _approved.get():
        if target is not None and action in ("remove", "revoke", "appoint") and target.protected_until and target.protected_until > wallclock.timestamp():
            raise Forbidden("the members voted that team in as a subcommander: only a vote of the members takes the role away for now")
        needed = builtin_percent(hooks, action)
        if needed is None and action in GOVERNED:
            needed = await rule_percent(session, alliance.id, action, actor.role)
        if needed is not None:
            raise NeedsVote(needed)
    return actor, target


# --- founding, inviting, joining --------------------------------------------------------------------------------------------

async def found(session: AsyncSession, hooks: Alliances, team: Team, name: str) -> Alliance:
    """The team founds an alliance and is its commander. The alliance starts with the game's ``starting_roles``."""
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
    session.add(AllianceMember(alliance_id=alliance.id, team_id=team.id, role=hooks.commander_role))
    session.add_all(AllianceRole(alliance_id=alliance.id, name=name, actions=sorted(actions)) for name, actions in hooks.starting_roles.items())
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
    await session.execute(delete(AllianceAlumnus).where(AllianceAlumnus.alliance_id == alliance.id, AllianceAlumnus.team_id == team.id))
    session.add(AllianceMember(alliance_id=alliance.id, team_id=team.id, role=hooks.default_role))
    await session.flush()
    return alliance


async def decline(session: AsyncSession, team: Team, alliance_id: int) -> None:
    gone = await session.execute(delete(AllianceInvite).where(AllianceInvite.alliance_id == alliance_id, AllianceInvite.team_id == team.id))
    if gone.rowcount == 0:
        raise NotFound("there's no such invitation")


# --- running it ------------------------------------------------------------------------------------------------------------------

async def _forget_votes(session: AsyncSession, team_id: int, alliance_id: int | None = None) -> None:
    """The team's votes in ballots that are still open (in one alliance, or in all of them) go when it stops being a member
    (what closed ballots counted stays in their results)."""
    open_ballots = select(Ballot.id).where(Ballot.closed_at.is_(None))
    if alliance_id is not None:
        open_ballots = open_ballots.where(Ballot.alliance_id == alliance_id)
    await session.execute(delete(BallotVote).where(BallotVote.team_id == team_id, BallotVote.ballot_id.in_(open_ballots)))
    await session.execute(delete(BallotVoter).where(BallotVoter.team_id == team_id, BallotVoter.ballot_id.in_(select(Ballot.id).where(
        Ballot.alliance_id == alliance_id if alliance_id is not None else Ballot.id.is_not(None)))))


async def _delete_alliance(session: AsyncSession, hooks: Alliances, alliance: Alliance) -> None:
    """Dissolves the alliance: its treasury is paid out in shares (``treasury.dissolve``), and everything of its own goes."""
    await treasury.dissolve(session, hooks, alliance)
    ballots = select(Ballot.id).where(Ballot.alliance_id == alliance.id)
    await session.execute(delete(BallotVote).where(BallotVote.ballot_id.in_(ballots)))
    await session.execute(delete(BallotVoter).where(BallotVoter.ballot_id.in_(ballots)))
    await session.execute(delete(Ballot).where(Ballot.alliance_id == alliance.id))
    await session.execute(delete(AllianceMember).where(AllianceMember.alliance_id == alliance.id))
    await session.execute(delete(AllianceInvite).where(AllianceInvite.alliance_id == alliance.id))
    await session.execute(delete(AllianceRole).where(AllianceRole.alliance_id == alliance.id))
    await session.execute(delete(AllianceRule).where(AllianceRule.alliance_id == alliance.id))
    await session.execute(delete(AllianceAlumnus).where(AllianceAlumnus.alliance_id == alliance.id))
    await session.execute(delete(AllianceProfile).where(AllianceProfile.alliance_id == alliance.id))
    await session.execute(delete(AllianceTop).where(AllianceTop.alliance_id == alliance.id))
    await session.execute(delete(AllianceKept).where(AllianceKept.alliance_id == alliance.id))
    await relations.forget(session, Ref("alliance", alliance.id))
    await session.delete(alliance)
    await session.flush()


async def leave(session: AsyncSession, hooks: Alliances, alliance: Alliance, team_id: int) -> bool:
    """The team leaves. The last team out takes the alliance with it (True); the commander can't leave while others
    remain: it hands the seat over first."""
    row = await member_row(session, alliance.id, team_id)
    if row is None:
        raise NotFound("that team is not in the alliance")
    others = [other for other in await members(session, alliance.id) if other.team_id != team_id]
    if not others:
        await _delete_alliance(session, hooks, alliance)
        return True
    if row.role == hooks.commander_role:
        raise AllianceError("hand the leadership over before leaving")
    await _forget_votes(session, team_id, alliance.id)
    await treasury.remember(session, alliance.id, team_id)
    await session.delete(row)
    await session.flush()
    return False


async def remove(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> None:
    _, target = await _acting(session, hooks, alliance, actor_team_id, "remove", target_team_id)
    await _forget_votes(session, target_team_id, alliance.id)
    await treasury.remember(session, alliance.id, target_team_id)
    await session.delete(target)
    await session.flush()


async def appoint(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int, role: str) -> AllianceMember:
    """The acting team gives another a role of the alliance. (The commander's seat is handed over, or filled by a
    subcommander when it is empty.)"""
    if role not in await role_names(session, hooks, alliance):
        raise NotFound("the alliance has no such role")
    _, target = await _acting(session, hooks, alliance, actor_team_id, "appoint", target_team_id, role)
    target.role, target.protected_until = role, None
    await session.flush()
    return target


async def revoke(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> AllianceMember:
    """The acting team takes a team's role away: it is a plain member again (the default role)."""
    _, target = await _acting(session, hooks, alliance, actor_team_id, "revoke", target_team_id)
    if target.role == hooks.default_role:
        raise AllianceError("that team holds no role to take away")
    target.role, target.protected_until = hooks.default_role, None
    await session.flush()
    return target


async def hand_over(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> None:
    """The commander gives the seat to another team and becomes a subcommander."""
    actor, target = await _acting(session, hooks, alliance, actor_team_id, "hand_over", target_team_id)
    target.role = hooks.commander_role
    actor.role = hooks.subcommander_role
    await session.flush()


# --- the roles of tier 3 ---------------------------------------------------------------------------------------------------------

async def role_names(session: AsyncSession, hooks: Alliances, alliance: Alliance) -> list[str]:
    """Every role the alliance has, highest tier first: the commander, subcommanders, then the roles it defined."""
    return [hooks.commander_role, hooks.subcommander_role, *await roles.names(session, alliance.id)]


async def role_actions(session: AsyncSession, alliance: Alliance) -> dict[str, list[str]]:
    rows = await session.execute(select(AllianceRole.name, AllianceRole.actions).where(AllianceRole.alliance_id == alliance.id).order_by(AllianceRole.id))
    return {name: sorted(held) for name, held in rows.all()}


async def define_role(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, name: str, actions: list[str]) -> dict[str, list[str]]:
    """Makes a role of tier 3 or changes the actions of one the alliance has (the default role included)."""
    await _acting(session, hooks, alliance, actor_team_id, "edit_roles")
    name = " ".join(name.split())
    if not 1 <= len(name) <= ROLE_MAX or not name.isprintable():
        raise AllianceError(f"a role's name is 1 to {ROLE_MAX} characters")
    if hooks.tier(name) != OTHER:
        raise AllianceError("the commander and subcommanders are not roles to define")
    unknown = sorted(set(actions) - set(ACTIONS))
    if unknown:
        raise AllianceError(f"there's no such action: {', '.join(unknown)}")
    row = await session.scalar(select(AllianceRole).where(AllianceRole.alliance_id == alliance.id, AllianceRole.name == name))
    if row is None:
        if len(await roles.names(session, alliance.id)) >= hooks.max_roles:
            raise AllianceError(f"an alliance has at most {hooks.max_roles} roles of its own")
        session.add(AllianceRole(alliance_id=alliance.id, name=name, actions=sorted(set(actions))))
    else:
        row.actions = sorted(set(actions))
    await session.flush()
    return await role_actions(session, alliance)


async def delete_role(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, name: str) -> dict[str, list[str]]:
    """Removes a role of tier 3; the teams that held it become plain members. The default role stays."""
    await _acting(session, hooks, alliance, actor_team_id, "edit_roles")
    if name == hooks.default_role:
        raise AllianceError("the role a team joins as can be changed but not removed")
    gone = await session.execute(delete(AllianceRole).where(AllianceRole.alliance_id == alliance.id, AllianceRole.name == name))
    if gone.rowcount == 0:
        raise NotFound("the alliance has no such role")
    await session.execute(update(AllianceMember).where(AllianceMember.alliance_id == alliance.id, AllianceMember.role == name).values(role=hooks.default_role))
    await session.flush()
    return await role_actions(session, alliance)


async def disband(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int) -> None:
    await _acting(session, hooks, alliance, actor_team_id, "disband")
    await _delete_alliance(session, hooks, alliance)


async def remove_team(session: AsyncSession, hooks: Alliances, team_id: int) -> None:
    """A team is going: it leaves every alliance (a commander's seat left empty stays so for the subcommanders to fill,
    and goes to the longest-standing team when there are none; the last team out takes the alliance) and its invitations go."""
    await session.execute(delete(AllianceInvite).where(AllianceInvite.team_id == team_id))
    for row in list((await session.scalars(select(AllianceMember).where(AllianceMember.team_id == team_id))).all()):
        alliance = await session.get(Alliance, row.alliance_id)
        others = [other for other in await members(session, alliance.id) if other.team_id != team_id]
        if not others:
            await _delete_alliance(session, hooks, alliance)
            continue
        if row.role == hooks.commander_role and not any(hooks.tier(other.role) == SUBCOMMANDER for other in others):
            min(others, key=lambda other: other.id).role = hooks.commander_role
        await session.delete(row)
    await _forget_votes(session, team_id)
    await session.flush()


# --- the treasury -------------------------------------------------------------------------------------------------------------------

async def deposit(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, amount: int) -> int:
    """The acting team gives gold to the treasury, from the purse of its first hero (the team's, in a team economy). Returns the balance."""
    await _acting(session, hooks, alliance, actor_team_id, "deposit")
    treasury._check(amount)
    hero = await session.scalar(select(Hero).join(TeamMember, TeamMember.hero_id == Hero.id).where(TeamMember.team_id == actor_team_id).order_by(TeamMember.slot).limit(1))
    if hero is None:
        raise treasury.TreasuryError("a team with no hero has no gold to give")
    try:
        await hooks.economy.debit(session, hero, amount)
    except NotEnoughGold as error:
        raise treasury.TreasuryError("not enough gold") from error
    await treasury.put(session, alliance, amount)
    return alliance.gold


async def spend(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, to_team_id: int, amount: int) -> int:
    """The acting team pays gold from the treasury to a member team. Returns the balance."""
    await _acting(session, hooks, alliance, actor_team_id, "spend", to_team_id)
    await treasury.take(session, alliance, amount)
    await hooks.economy.credit_team(session, to_team_id, amount)
    return alliance.gold


# --- voting requirements and the members' own actions ---------------------------------------------------------------------------

async def rules(session: AsyncSession, alliance: Alliance) -> list[dict]:
    rows = (await session.scalars(select(AllianceRule).where(AllianceRule.alliance_id == alliance.id).order_by(AllianceRule.id))).all()
    return [{"action": row.action, "role": row.role, "percent": row.percent} for row in rows]


async def set_rule(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, action: str, role: str, percent: int | None) -> list[dict]:
    """Asks for a vote of ``percent`` of the active teams when a team in ``role`` (empty: any role) does ``action``, or
    drops the requirement (``percent`` None). Changing voting rules is an action like any other: it needs a vote when a
    rule says so."""
    await _acting(session, hooks, alliance, actor_team_id, "set_rules")
    if action not in GOVERNED:
        raise AllianceError("there's no vote to ask for on that")
    if role and role not in await role_names(session, hooks, alliance):
        raise NotFound("the alliance has no such role")
    if percent is not None and not 1 <= percent <= 100:
        raise AllianceError("a vote needs 1 to 100 percent")
    where = (AllianceRule.alliance_id == alliance.id, AllianceRule.action == action, AllianceRule.role == role)
    if percent is None:
        await session.execute(delete(AllianceRule).where(*where))
    else:
        row = await session.scalar(select(AllianceRule).where(*where))
        if row is None:
            session.add(AllianceRule(alliance_id=alliance.id, action=action, role=role, percent=percent))
        else:
            row.percent = percent
    await session.flush()
    return await rules(session, alliance)


async def elect_subcommander(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> AllianceMember:
    """Runs when the members have voted a team in: a subcommander the commander cannot remove for ``subcommander_protection``."""
    _, target = await _acting(session, hooks, alliance, actor_team_id, "elect_subcommander", target_team_id)
    target.role, target.protected_until = hooks.subcommander_role, wallclock.timestamp() + hooks.subcommander_protection
    await session.flush()
    return target


async def dismiss_subcommander(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, target_team_id: int) -> AllianceMember:
    _, target = await _acting(session, hooks, alliance, actor_team_id, "dismiss_subcommander", target_team_id)
    target.role, target.protected_until = hooks.default_role, None
    await session.flush()
    return target


async def remove_commander(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int) -> AllianceMember:
    """Runs when the subcommanders have voted unanimously: the commander is a plain member and the seat is empty."""
    commander = await session.scalar(select(AllianceMember).where(AllianceMember.alliance_id == alliance.id, AllianceMember.role == hooks.commander_role))
    if commander is None:
        raise NotFound("there is no commander")
    await _acting(session, hooks, alliance, actor_team_id, "remove_commander", commander.team_id)
    commander.role = hooks.default_role
    await session.flush()
    return commander


#: What a ballot can carry: the name it is called by, the action it is (what the roles and rules know it as) and what runs.
RUNS = {
    "invite": ("invite", invite),
    "withdraw": ("withdraw", withdraw),
    "remove": ("remove", remove),
    "appoint": ("appoint", appoint),
    "revoke": ("revoke", revoke),
    "hand_over": ("hand_over", hand_over),
    "disband": ("disband", disband),
    "define_role": ("edit_roles", define_role),
    "delete_role": ("edit_roles", delete_role),
    "set_rule": ("set_rules", set_rule),
    "deposit": ("deposit", deposit),
    "spend": ("spend", spend),
    "elect_subcommander": ("elect_subcommander", elect_subcommander),
    "dismiss_subcommander": ("dismiss_subcommander", dismiss_subcommander),
    "remove_commander": ("remove_commander", remove_commander),
}


async def run_approved(session: AsyncSession, hooks: Alliances, alliance: Alliance, actor_team_id: int, run: str, args: dict) -> None:
    """Does what a passed ballot carries, as the team that put it, whose role must still allow it."""
    with approved():
        await RUNS[run][1](session, hooks, alliance, actor_team_id, **args)


# --- what the player sees --------------------------------------------------------------------------------------------------------------

async def view(session: AsyncSession, hooks: Alliances, alliance: Alliance, viewer_team_id: int) -> dict:
    """The alliance as one of its members sees it: the roles, the teams, and (if its role may invite) who is invited."""
    rows = await members(session, alliance.id)
    names = {team.id: team.name for team in (await session.scalars(select(Team).where(Team.id.in_([row.team_id for row in rows])))).all()}
    me = next(row for row in rows if row.team_id == viewer_team_id)
    every = await role_names(session, hooks, alliance)
    can = [action for action in ACTIONS if await hooks.allowed(session, alliance, me, action)]
    shown = {
        "id": alliance.id, "name": alliance.name, "roles": every, "gold": alliance.gold, "role_actions": await role_actions(session, alliance), "rules": await rules(session, alliance), "you": {"team_id": me.team_id, "role": me.role},
        "can": can, "appointable": [role for role in every if await hooks.allowed(session, alliance, me, "appoint", role=role)],
        "members": [
            {"team_id": row.team_id, "team": names.get(row.team_id), "role": row.role, "appoint": await hooks.allowed(session, alliance, me, "appoint", row)}
            for row in rows
        ],
    }
    if "invite" in can or "withdraw" in can:
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
    for row in sorted(owned, key=lambda each: (hooks.tier(each.role), each.id)):
        if await hooks.allowed(session, alliance, row, action):
            return row
    raise Forbidden("your role can't do that")


async def reading_team(session: AsyncSession, account: Account, alliance: Alliance) -> AllianceMember:
    """One of the account's teams in the alliance (any role), or NotFound."""
    for row in await members(session, alliance.id):
        if (await session.get(Team, row.team_id)).account_id == account.id:
            return row
    raise NotFound("there's no such alliance")
