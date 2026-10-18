"""Voting on alliance actions: rules that ask for a vote, ballots that carry an action, active teams, and the members' own votes
(electing and dismissing a subcommander, removing the commander)."""

import pytest
from sqlalchemy import func, select, update

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.alliances import ballots, service
from terraforma.alliances.hooks import Alliances
from terraforma.alliances.models import Alliance, AllianceInvite, AllianceMember, AllianceRule, Ballot
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Team
from terraforma.models import Account
from terraforma.parties import service as parties

pytestmark = pytest.mark.social

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
RULES = Alliances()
WEEK = 7 * 24 * 3600


async def setup(db, count=3):
    """An alliance of a commander (Lead) and ``count`` more teams (T1, T2, ...), all plain members, all active."""
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    lead = await heroes.create_team(db, mike, "Lead")
    alliance = await service.found(db, RULES, lead, "Pact")
    others = []
    for number in range(1, count + 1):
        team = await heroes.create_team(db, mike, f"T{number}")
        await service.invite(db, RULES, alliance, lead.id, team.id)
        await service.accept(db, RULES, team, alliance.id)
        others.append(team)
    await db.execute(update(Team).values(last_played_at=wallclock.timestamp()))
    return alliance, lead, others


async def roles(db, alliance):
    return {row.team_id: row.role for row in await service.members(db, alliance.id)}


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


# --- who is active -----------------------------------------------------------------------------------------------------------

@anyio
async def test_a_team_is_active_from_the_moment_it_is_selected_to_play(db):
    await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    team = await heroes.create_team(db, mike, "Lead")
    assert team.last_played_at is None
    await heroes.add_to_team(db, mike, team.id, (await heroes.create_hero(db, mike, "Aria", "fighter")).id)
    await parties.play(db, team.id, 4)
    assert abs((await db.get(Team, team.id)).last_played_at - wallclock.timestamp()) <= 2


# --- rules ---------------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_rule_asks_for_a_vote_and_the_action_waits_for_it(db):
    alliance, lead, (one, two, three) = await setup(db)
    outsider = await heroes.create_team(db, await db.get(Account, lead.account_id), "Guest")
    await service.set_rule(db, RULES, alliance, lead.id, "invite", "", 60)
    with pytest.raises(service.NeedsVote, match="60%"):
        await service.invite(db, RULES, alliance, lead.id, outsider.id)  # (nor does the service do it by the back door)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "invite", {"team_id": outsider.id})
    assert (ballot.kind, ballot.threshold, ballot.title, ballot.payload) == ("action", 60, "Invite Guest", {"run": "invite", "args": {"team_id": outsider.id}, "by": lead.id})
    assert await count(db, AllianceInvite) == 0, "trying it first leaves nothing behind"
    assert ballot.closed_at is None, "1 of 4 active teams voted (the proposer)"
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert ballot.closed_at is None, "2 of 4 is 50%"
    await ballots.cast(db, RULES, alliance, two.id, ballot, 0)
    assert ballot.result == {"winner": 0, "totals": [3, 0], "turnout": 3, "eligible": 4, "threshold": 60, "active": 4, "passed": True, "executed": True, "error": None}
    assert await count(db, AllianceInvite) == 1, "it passed as soon as it could, and was done"


@anyio
async def test_teams_that_have_not_played_for_a_week_do_not_count_toward_the_percent(db):
    alliance, lead, (one, two, three) = await setup(db)
    await db.execute(update(Team).where(Team.id.in_([one.id, two.id, three.id])).values(last_played_at=wallclock.timestamp() - WEEK - 60))
    await service.set_rule(db, RULES, alliance, lead.id, "remove", "", 100)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "remove", {"target_team_id": three.id})
    assert ballot.closed_at is not None and ballot.result["passed"] and ballot.result["active"] == 1, "the one active team is all of the active teams"
    assert set(await roles(db, alliance)) == {lead.id, one.id, two.id}


@anyio
async def test_a_ballot_that_does_not_reach_its_percent_by_its_time_does_nothing(db, later):
    alliance, lead, (one, two, three) = await setup(db)
    await service.set_rule(db, RULES, alliance, lead.id, "remove", "", 75)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "remove", {"target_team_id": three.id})
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await ballots.cast(db, RULES, alliance, two.id, ballot, 1)
    later(RULES.action_ballot_seconds)
    await ballots.refresh(db, RULES, alliance, ballot)
    assert ballot.result["passed"] is False and ballot.result["winner"] is None and "executed" not in ballot.result
    assert set(await roles(db, alliance)) == {lead.id, one.id, two.id, three.id}


@anyio
async def test_a_rule_can_be_for_one_role_and_a_role_without_one_acts_freely(db):
    alliance, lead, (one, two, three) = await setup(db)
    await service.appoint(db, RULES, alliance, lead.id, one.id, "subcommander")
    await service.set_rule(db, RULES, alliance, lead.id, "remove", "commander", 50)
    await service.remove(db, RULES, alliance, one.id, three.id)  # a subcommander: no rule for the role, no vote
    with pytest.raises(service.NeedsVote):
        await service.remove(db, RULES, alliance, lead.id, two.id)
    rows = await service.rules(db, alliance)
    assert rows == [{"action": "remove", "role": "commander", "percent": 50}]
    await service.set_rule(db, RULES, alliance, one.id, "remove", "commander", None)  # dropping it
    await service.remove(db, RULES, alliance, lead.id, two.id)
    assert await count(db, AllianceRule) == 0


@anyio
async def test_changing_the_voting_rules_can_itself_need_a_vote(db):
    alliance, lead, (one, two, three) = await setup(db)
    await service.set_rule(db, RULES, alliance, lead.id, "set_rules", "", 60)
    with pytest.raises(service.NeedsVote):
        await service.set_rule(db, RULES, alliance, lead.id, "invite", "", 60)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "set_rule", {"action": "invite", "role": "", "percent": 60})
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await ballots.cast(db, RULES, alliance, two.id, ballot, 0)
    assert ballot.result["executed"] and len(await service.rules(db, alliance)) == 2


@anyio
async def test_rules_are_checked_for_what_they_name(db):
    alliance, lead, (one, *_) = await setup(db)
    for action, role, percent, error in (
        ("vote", "", 50, service.AllianceError), ("fly", "", 50, service.AllianceError),
        ("invite", "captain", 50, service.NotFound), ("invite", "", 0, service.AllianceError), ("invite", "", 101, service.AllianceError),
    ):
        with pytest.raises(error):
            await service.set_rule(db, RULES, alliance, lead.id, action, role, percent)
    with pytest.raises(service.Forbidden):
        await service.set_rule(db, RULES, alliance, one.id, "invite", "", 50)  # a member


# --- putting it to a vote when no rule says to ---------------------------------------------------------------------------------

@anyio
async def test_a_team_may_put_an_action_to_a_vote_when_no_rule_asks(db):
    alliance, lead, (one, two, three) = await setup(db)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "remove", {"target_team_id": three.id}, vote=True)
    assert ballot.threshold == RULES.voluntary_percent and ballot.closed_at is None
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert ballot.result["executed"] and three.id not in await roles(db, alliance), "2 of 4 is half, and a vote with no rule of its own asks for half"
    with pytest.raises(ballots.BallotError, match="closed"):
        await ballots.cast(db, RULES, alliance, two.id, ballot, 0)


@anyio
async def test_a_vote_that_cannot_be_done_is_refused_before_it_opens_and_one_that_fails_later_says_why(db):
    alliance, lead, (one, two, three) = await setup(db)
    with pytest.raises(service.Forbidden):
        await ballots.perform(db, RULES, alliance, one.id, "remove", {"target_team_id": two.id}, vote=True)  # a member can't remove anyone
    with pytest.raises(service.NotFound):
        await ballots.perform(db, RULES, alliance, lead.id, "remove", {"target_team_id": 999_999}, vote=True)
    assert await count(db, Ballot) == 0
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "remove", {"target_team_id": three.id}, vote=True, closes_in=3600)
    await service.leave(db, RULES, alliance, three.id)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert ballot.result["passed"] and ballot.result["executed"] is False and "not in the alliance" in ballot.result["error"]


@anyio
async def test_disbanding_by_a_vote_takes_the_alliance_and_its_ballots_with_it(db):
    alliance, lead, (one, *_) = await setup(db)
    ballot = await ballots.perform(db, RULES, alliance, lead.id, "disband", {}, vote=True)
    assert await count(db, Alliance) == 1, "tried and taken back"
    assert await count(db, Ballot) == 1 and ballot.closed_at is None
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    assert await count(db, Alliance) == 0 and await count(db, Ballot) == 0


@anyio
async def test_an_ordinary_ballot_cannot_pose_as_one_that_carries_an_action(db):
    alliance, lead, _ = await setup(db, 1)
    with pytest.raises(ballots.BallotError, match="engine's"):
        await ballots.open_ballot(db, RULES, alliance, lead.id, "Sneaky", ["Yes", "No"], kind="action", payload={"run": "disband", "args": {}, "by": lead.id})


# --- the members' own votes ------------------------------------------------------------------------------------------------------------

@anyio
async def test_the_members_elect_a_subcommander_the_commander_cannot_remove_for_a_week(db, later):
    alliance, lead, (one, two, three) = await setup(db)
    with pytest.raises(service.NeedsVote, match="60%"):
        await service.elect_subcommander(db, RULES, alliance, one.id, two.id)
    with pytest.raises(service.Forbidden):
        await ballots.perform(db, RULES, alliance, lead.id, "elect_subcommander", {"target_team_id": two.id})  # (the commander just appoints)
    ballot = await ballots.perform(db, RULES, alliance, one.id, "elect_subcommander", {"target_team_id": two.id})
    assert ballot.threshold == 60 and ballot.closed_at is None
    await ballots.cast(db, RULES, alliance, lead.id, ballot, 0)
    assert ballot.closed_at is None, "2 of 4 is 50%"
    await ballots.cast(db, RULES, alliance, three.id, ballot, 0)
    assert ballot.result["executed"] and (await roles(db, alliance))[two.id] == "subcommander"
    for attempt in (service.remove(db, RULES, alliance, lead.id, two.id), service.revoke(db, RULES, alliance, lead.id, two.id), service.appoint(db, RULES, alliance, lead.id, two.id, "member")):
        with pytest.raises(service.Forbidden, match="voted that team in"):
            await attempt
    later(WEEK)
    await service.revoke(db, RULES, alliance, lead.id, two.id)
    assert (await roles(db, alliance))[two.id] == "member"


@anyio
async def test_the_members_can_dismiss_a_subcommander_even_within_the_week(db):
    alliance, lead, (one, two, three) = await setup(db)
    elect = await ballots.perform(db, RULES, alliance, one.id, "elect_subcommander", {"target_team_id": two.id})
    for team in (lead, three):
        await ballots.cast(db, RULES, alliance, team.id, elect, 0)
    assert (await roles(db, alliance))[two.id] == "subcommander"
    with pytest.raises(service.Forbidden):
        await ballots.perform(db, RULES, alliance, one.id, "dismiss_subcommander", {"target_team_id": three.id})  # not one
    dismiss = await ballots.perform(db, RULES, alliance, one.id, "dismiss_subcommander", {"target_team_id": two.id})
    for team in (lead, three):
        await ballots.cast(db, RULES, alliance, team.id, dismiss, 0)
    assert (await roles(db, alliance))[two.id] == "member"


@anyio
async def test_the_subcommanders_remove_the_commander_only_all_together_and_then_fill_the_seat(db):
    alliance, lead, (one, two, three) = await setup(db)
    for team in (one, two):
        await service.appoint(db, RULES, alliance, lead.id, team.id, "subcommander")
    with pytest.raises(service.Forbidden):
        await ballots.perform(db, RULES, alliance, three.id, "remove_commander", {})  # a member
    with pytest.raises(service.NeedsVote, match="100%"):
        await service.remove_commander(db, RULES, alliance, one.id)
    ballot = await ballots.perform(db, RULES, alliance, one.id, "remove_commander", {})
    assert (ballot.threshold, ballot.electorate) == (100, ["subcommander"]) and ballot.closed_at is None
    with pytest.raises(service.Forbidden, match="can't vote"):
        await ballots.cast(db, RULES, alliance, three.id, ballot, 0)  # only the subcommanders vote on it
    await ballots.cast(db, RULES, alliance, two.id, ballot, 0)
    assert ballot.result["executed"] and (await roles(db, alliance))[lead.id] == "member"
    await service.appoint(db, RULES, alliance, one.id, two.id, "commander")  # a subcommander fills the empty seat
    assert (await roles(db, alliance))[two.id] == "commander"


@anyio
async def test_a_subcommander_who_does_not_agree_keeps_the_commander(db, later):
    alliance, lead, (one, two, _three) = await setup(db)
    for team in (one, two):
        await service.appoint(db, RULES, alliance, lead.id, team.id, "subcommander")
    ballot = await ballots.perform(db, RULES, alliance, one.id, "remove_commander", {})
    await ballots.cast(db, RULES, alliance, two.id, ballot, 1)
    assert ballot.closed_at is not None and ballot.result["passed"] is False
    assert (await roles(db, alliance))[lead.id] == "commander"
