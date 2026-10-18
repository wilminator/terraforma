"""Alliances of teams: founding, invitations, the three tiers of roles, leaving and disbanding, and standing in relationships."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account
from terraforma.alliances import service
from terraforma.alliances.hooks import ACTIONS, Alliances
from terraforma.alliances.models import Alliance, AllianceInvite, AllianceMember, AllianceRole
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.relations import service as relating
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.models import Relationship
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect

pytestmark = pytest.mark.social

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
RULES = Alliances()


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the first account)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def teams(db, owner, *names):
    return [await heroes.create_team(db, owner, name) for name in names]


async def roles(db, alliance):
    return {row.team_id: row.role for row in await service.members(db, alliance.id)}


async def join(db, alliance, founder, team, rules=RULES):
    await service.invite(db, rules, alliance, founder.id, team.id)
    await service.accept(db, rules, team, alliance.id)


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


# --- founding ---------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_team_founds_an_alliance_and_is_its_commander(db):
    mike = await account(db)
    (vanguard,) = await teams(db, mike, "Vanguard")
    alliance = await service.found(db, RULES, vanguard, "  The   Iron Pact ")
    assert alliance.name == "The Iron Pact" and await roles(db, alliance) == {vanguard.id: "commander"}


@anyio
async def test_names_are_unique_ignoring_case_and_spacing_and_must_be_real(db):
    mike = await account(db)
    one, two = await teams(db, mike, "One", "Two")
    await service.found(db, RULES, one, "Iron Pact")
    for name in ("iron  PACT", " IRON pact"):
        with pytest.raises(service.AllianceError, match="already has that name"):
            await service.found(db, RULES, two, name)
    for name in ("", "   ", "!!!", "x" * 33):
        with pytest.raises(service.AllianceError):
            await service.found(db, RULES, two, name)


@anyio
async def test_a_team_is_in_only_so_many_alliances_and_the_game_may_say_who_founds(db):
    mike = await account(db)
    (one,) = await teams(db, mike, "One")
    for number in range(RULES.max_per_team):
        await service.found(db, RULES, one, f"Pact {number}")
    with pytest.raises(service.AllianceError, match="can be in 3 alliances"):
        await service.found(db, RULES, one, "One too many")

    class Closed(Alliances):
        async def may_found(self, session, team):
            return False

    (two,) = await teams(db, mike, "Two")
    with pytest.raises(service.Forbidden, match="can't found"):
        await service.found(db, Closed(), two, "Never")


# --- invitations --------------------------------------------------------------------------------------------------------------

@anyio
async def test_an_invited_team_joins_in_the_default_role(db):
    mike = await account(db)
    vanguard, rearguard = await teams(db, mike, "Vanguard", "Rearguard")
    alliance = await service.found(db, RULES, vanguard, "Pact")
    await service.invite(db, RULES, alliance, vanguard.id, rearguard.id)
    assert await count(db, AllianceInvite) == 1 and await roles(db, alliance) == {vanguard.id: "commander"}
    joined = await service.accept(db, RULES, rearguard, alliance.id)
    assert joined.id == alliance.id and await roles(db, alliance) == {vanguard.id: "commander", rearguard.id: "member"}
    assert await count(db, AllianceInvite) == 0


@anyio
async def test_only_a_role_that_may_invite_does_and_never_twice_or_to_a_member(db):
    mike = await account(db)
    vanguard, rearguard, scouts, cooks = await teams(db, mike, "Vanguard", "Rearguard", "Scouts", "Cooks")
    alliance = await service.found(db, RULES, vanguard, "Pact")
    await join(db, alliance, vanguard, rearguard)
    with pytest.raises(service.Forbidden, match="role can't"):
        await service.invite(db, RULES, alliance, rearguard.id, scouts.id)  # a member
    with pytest.raises(service.Forbidden, match="not in the alliance"):
        await service.invite(db, RULES, alliance, cooks.id, scouts.id)  # an outsider
    await service.appoint(db, RULES, alliance, vanguard.id, rearguard.id, "recruiter")
    await service.invite(db, RULES, alliance, rearguard.id, scouts.id)  # a recruiter
    with pytest.raises(service.AllianceError, match="invited already"):
        await service.invite(db, RULES, alliance, vanguard.id, scouts.id)
    with pytest.raises(service.AllianceError, match="already in the alliance"):
        await service.invite(db, RULES, alliance, vanguard.id, rearguard.id)
    with pytest.raises(service.NotFound):
        await service.invite(db, RULES, alliance, vanguard.id, 999_999)


@anyio
async def test_an_alliance_holds_only_so_many_teams_counting_those_invited(db):
    mike = await account(db)
    one, two, three = await teams(db, mike, "One", "Two", "Three")

    class Small(Alliances):
        max_members = 2

    alliance = await service.found(db, Small(), one, "Pact")
    await service.invite(db, Small(), alliance, one.id, two.id)
    with pytest.raises(service.AllianceError, match="holds 2 teams"):
        await service.invite(db, Small(), alliance, one.id, three.id)
    await service.decline(db, two, alliance.id)
    await service.invite(db, Small(), alliance, one.id, three.id)


@anyio
async def test_declining_and_withdrawing_an_invitation(db):
    mike = await account(db)
    one, two, three = await teams(db, mike, "One", "Two", "Three")
    alliance = await service.found(db, RULES, one, "Pact")
    await service.invite(db, RULES, alliance, one.id, two.id)
    await service.invite(db, RULES, alliance, one.id, three.id)
    await service.decline(db, two, alliance.id)
    await service.withdraw(db, RULES, alliance, one.id, three.id)
    assert await count(db, AllianceInvite) == 0
    for attempt in (service.decline(db, two, alliance.id), service.accept(db, RULES, two, alliance.id), service.withdraw(db, RULES, alliance, one.id, two.id)):
        with pytest.raises(service.NotFound):
            await attempt


@anyio
async def test_the_game_may_refuse_a_team_that_has_been_invited(db):
    mike = await account(db)
    one, two = await teams(db, mike, "One", "Two")

    class Picky(Alliances):
        async def may_join(self, session, alliance, team):
            return team.name != "Two"

    alliance = await service.found(db, Picky(), one, "Pact")
    await service.invite(db, Picky(), alliance, one.id, two.id)
    with pytest.raises(service.Forbidden, match="can't join"):
        await service.accept(db, Picky(), two, alliance.id)
    assert await roles(db, alliance) == {one.id: "commander"}


# --- running it ----------------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_new_alliance_has_the_games_starting_roles_and_the_commander_holds_the_seat(db):
    mike = await account(db)
    (lead,) = await teams(db, mike, "Lead")
    alliance = await service.found(db, RULES, lead, "Pact")
    assert await service.role_names(db, RULES, alliance) == ["commander", "subcommander", "treasurer", "quartermaster", "diplomat", "recruiter", "member"]
    assert await service.role_actions(db, alliance) == {name: sorted(held) for name, held in RULES.starting_roles.items()}
    assert (await service.role_actions(db, alliance))["treasurer"] == ["deposit", "set_fight_tax", "set_sales_tax", "spend", "vote"]
    assert await count(db, AllianceRole) == 5


@anyio
async def test_the_commander_does_everything_and_a_subcommander_everything_but_to_the_commander(db):
    mike = await account(db)
    lead, deputy, other, newbie = await teams(db, mike, "Lead", "Deputy", "Other", "Newbie")
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in (deputy, other, newbie):
        await join(db, alliance, lead, team)
    await service.appoint(db, RULES, alliance, lead.id, deputy.id, "subcommander")
    await service.appoint(db, RULES, alliance, deputy.id, other.id, "treasurer")  # a subcommander appoints too
    assert await roles(db, alliance) == {lead.id: "commander", deputy.id: "subcommander", other.id: "treasurer", newbie.id: "member"}
    for attempt in (
        service.remove(db, RULES, alliance, deputy.id, lead.id),
        service.revoke(db, RULES, alliance, deputy.id, lead.id),
        service.appoint(db, RULES, alliance, deputy.id, lead.id, "member"),
        service.appoint(db, RULES, alliance, deputy.id, newbie.id, "commander"),  # the seat is taken
        service.hand_over(db, RULES, alliance, deputy.id, newbie.id),  # the seat is the commander's to give
        service.appoint(db, RULES, alliance, lead.id, newbie.id, "commander"),  # and is given by handing over
        service.remove(db, RULES, alliance, lead.id, lead.id),  # nobody acts on themselves
    ):
        with pytest.raises(service.Forbidden):
            await attempt
    with pytest.raises(service.NotFound, match="no such role"):
        await service.appoint(db, RULES, alliance, lead.id, newbie.id, "captain")
    await service.revoke(db, RULES, alliance, deputy.id, other.id)
    assert (await roles(db, alliance))[other.id] == "member"
    with pytest.raises(service.AllianceError, match="holds no role"):
        await service.revoke(db, RULES, alliance, lead.id, other.id)
    await service.remove(db, RULES, alliance, deputy.id, newbie.id)
    assert set(await roles(db, alliance)) == {lead.id, deputy.id, other.id}


@anyio
async def test_a_subcommander_fills_an_empty_seat_and_a_commander_leaves_it_empty_by_leaving_the_game(db):
    mike = await account(db)
    lead, deputy, other = await teams(db, mike, "Lead", "Deputy", "Other")
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in (deputy, other):
        await join(db, alliance, lead, team)
    await service.appoint(db, RULES, alliance, lead.id, deputy.id, "subcommander")
    await heroes.delete_team(db, mike, lead.id)
    assert await roles(db, alliance) == {deputy.id: "subcommander", other.id: "member"}, "a subcommander is there to fill the seat"
    await service.appoint(db, RULES, alliance, deputy.id, other.id, "commander")
    assert await roles(db, alliance) == {deputy.id: "subcommander", other.id: "commander"}


@anyio
async def test_a_tier_three_role_acts_only_as_its_actions_say_and_only_on_its_own_tier(db):
    mike = await account(db)
    lead, clerk, deputy, member, scout = await teams(db, mike, "Lead", "Clerk", "Deputy", "Member", "Scout")
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in (clerk, deputy, member):
        await join(db, alliance, lead, team)
    await service.appoint(db, RULES, alliance, lead.id, clerk.id, "recruiter")
    await service.appoint(db, RULES, alliance, lead.id, deputy.id, "subcommander")
    await service.invite(db, RULES, alliance, clerk.id, scout.id)  # a recruiter invites
    await service.withdraw(db, RULES, alliance, clerk.id, scout.id)
    for attempt in (
        service.remove(db, RULES, alliance, clerk.id, member.id),  # it has no `remove`
        service.appoint(db, RULES, alliance, clerk.id, member.id, "diplomat"),  # nor `appoint`
        service.hand_over(db, RULES, alliance, clerk.id, member.id),
        service.disband(db, RULES, alliance, clerk.id),
    ):
        with pytest.raises(service.Forbidden):
            await attempt
    await service.define_role(db, RULES, alliance, lead.id, "recruiter", ["invite", "withdraw", "remove", "appoint", "revoke", "vote"])
    await service.appoint(db, RULES, alliance, clerk.id, member.id, "diplomat")  # now it appoints, but only within tier three
    with pytest.raises(service.Forbidden):
        await service.appoint(db, RULES, alliance, clerk.id, member.id, "subcommander")
    for attempt in (
        service.remove(db, RULES, alliance, clerk.id, deputy.id),
        service.remove(db, RULES, alliance, clerk.id, lead.id),
        service.revoke(db, RULES, alliance, clerk.id, deputy.id),
    ):
        with pytest.raises(service.Forbidden):
            await attempt
    await service.revoke(db, RULES, alliance, clerk.id, member.id)
    await service.remove(db, RULES, alliance, clerk.id, member.id)


@anyio
async def test_an_alliance_defines_and_removes_its_own_roles(db):
    mike = await account(db)
    lead, clerk, member = await teams(db, mike, "Lead", "Clerk", "Member")
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in (clerk, member):
        await join(db, alliance, lead, team)
    await service.define_role(db, RULES, alliance, lead.id, "  Scribe ", ["speak", "vote", "speak"])
    assert (await service.role_actions(db, alliance))["Scribe"] == ["speak", "vote"]
    await service.appoint(db, RULES, alliance, lead.id, clerk.id, "Scribe")
    assert await service.speaking_team(db, RULES, mike, alliance)
    with pytest.raises(service.Forbidden):
        await service.define_role(db, RULES, alliance, clerk.id, "Spy", ["speak"])  # without `edit_roles`
    for name, actions, message in (
        ("", [], "1 to 16"), ("x" * 17, [], "1 to 16"), ("commander", [], "not roles to define"), ("subcommander", [], "not roles to define"),
        ("Spy", ["fly"], "no such action"),
    ):
        with pytest.raises(service.AllianceError, match=message):
            await service.define_role(db, RULES, alliance, lead.id, name, actions)

    class Few(Alliances):
        max_roles = 5

    with pytest.raises(service.AllianceError, match="at most 5 roles"):
        await service.define_role(db, Few(), alliance, lead.id, "Sixth", [])
    await service.delete_role(db, RULES, alliance, lead.id, "Scribe")
    assert (await roles(db, alliance))[clerk.id] == "member", "a team in a removed role is a plain member"
    with pytest.raises(service.AllianceError, match="not removed"):
        await service.delete_role(db, RULES, alliance, lead.id, "member")
    with pytest.raises(service.NotFound):
        await service.delete_role(db, RULES, alliance, lead.id, "Scribe")
    await service.define_role(db, RULES, alliance, lead.id, "member", [])  # the default role may be changed
    assert (await service.role_actions(db, alliance))["member"] == []


@anyio
async def test_the_commander_hands_the_seat_over_and_can_then_leave(db):
    mike = await account(db)
    lead, other = await teams(db, mike, "Lead", "Other")
    alliance = await service.found(db, RULES, lead, "Pact")
    await join(db, alliance, lead, other)
    with pytest.raises(service.AllianceError, match="hand the leadership over"):
        await service.leave(db, RULES, alliance, lead.id)
    await service.hand_over(db, RULES, alliance, lead.id, other.id)
    assert await roles(db, alliance) == {lead.id: "subcommander", other.id: "commander"}
    assert await service.leave(db, RULES, alliance, lead.id) is False
    assert await roles(db, alliance) == {other.id: "commander"}
    with pytest.raises(service.Forbidden):
        await service.hand_over(db, RULES, alliance, lead.id, other.id)  # no longer in it


@anyio
async def test_the_last_team_out_takes_the_alliance_with_it_and_so_does_disbanding(db):
    mike = await account(db)
    one, two, three = await teams(db, mike, "One", "Two", "Three")
    alliance = await service.found(db, RULES, one, "Pact")
    await service.invite(db, RULES, alliance, one.id, two.id)
    assert await service.leave(db, RULES, alliance, one.id) is True
    assert await count(db, Alliance) == 0 and await count(db, AllianceMember) == 0 and await count(db, AllianceInvite) == 0 and await count(db, AllianceRole) == 0
    second = await service.found(db, RULES, one, "Second")
    await join(db, second, one, three)
    await relating.apply(db, Relations(), Change(Ref("alliance", second.id), Ref("team", two.id), score=-50))
    with pytest.raises(service.Forbidden):
        await service.disband(db, RULES, second, three.id)
    await service.disband(db, RULES, second, one.id)
    assert await count(db, Alliance) == 0 and await count(db, Relationship) == 0, "its relationships go with it"


@anyio
async def test_a_team_deleted_leaves_its_alliances_and_the_longest_standing_team_takes_an_unfilled_seat(db):
    mike = await account(db)
    lead, first, second, last = await teams(db, mike, "Lead", "First", "Second", "Last")
    alliance = await service.found(db, RULES, lead, "Pact")
    for team in (first, second, last):
        await join(db, alliance, lead, team)
    await heroes.delete_team(db, mike, lead.id)
    assert await roles(db, alliance) == {first.id: "commander", second.id: "member", last.id: "member"}, "no subcommander to fill it: the longest-standing"
    await service.invite(db, RULES, alliance, first.id, (await teams(db, mike, "Guest"))[0].id)
    for team in (first, second, last):
        await heroes.delete_team(db, mike, team.id)
    assert await count(db, Alliance) == 0 and await count(db, AllianceMember) == 0 and await count(db, AllianceInvite) == 0 and await count(db, AllianceRole) == 0


# --- the game's own roles ----------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_game_names_the_top_tiers_and_chooses_the_roles_a_new_alliance_starts_with(db):
    mike = await account(db)
    a, b, c = await teams(db, mike, "Aa", "Bb", "Cc")

    class Guild(Alliances):
        commander_role, subcommander_role = "master", "warden"
        default_role = "squire"
        starting_roles = {"squire": {"vote"}, "herald": {"speak", "vote"}}

    guild = Guild()
    alliance = await service.found(db, guild, a, "Order")
    await join(db, alliance, a, b, guild)
    assert await roles(db, alliance) == {a.id: "master", b.id: "squire"}
    assert await service.role_names(db, guild, alliance) == ["master", "warden", "squire", "herald"]
    await service.appoint(db, guild, alliance, a.id, b.id, "warden")
    await service.invite(db, guild, alliance, b.id, c.id)  # a warden may do everything
    await service.accept(db, guild, c, alliance.id)
    await service.appoint(db, guild, alliance, a.id, c.id, "herald")
    with pytest.raises(service.Forbidden):
        await service.remove(db, guild, alliance, c.id, b.id)  # a herald can't
    await service.hand_over(db, guild, alliance, a.id, b.id)
    assert await roles(db, alliance) == {a.id: "warden", b.id: "master", c.id: "herald"}


@anyio
async def test_the_game_can_say_otherwise_with_allowed(db):
    mike = await account(db)
    one, two = await teams(db, mike, "One", "Two")

    class Equals(Alliances):
        async def allowed(self, session, alliance, actor, action, target=None, role=None):
            return True  # everyone may do everything

    alliance = await service.found(db, Equals(), one, "Pact")
    await join(db, alliance, one, two, Equals())
    await service.remove(db, Equals(), alliance, two.id, one.id)  # the member removes the commander
    assert await roles(db, alliance) == {two.id: "member"}


# --- standing in relationships -----------------------------------------------------------------------------------------------------------------

@anyio
async def test_an_alliance_has_relationships_and_a_role_that_speaks_for_it(db):
    mike = await account(db)
    zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    lead, member, rivals = await teams(db, mike, "Lead", "Member", "Rivals")
    (zeds,) = await teams(db, zed, "Zeds")
    alliance = await service.found(db, RULES, lead, "Pact")
    await join(db, alliance, lead, member)
    assert (await service.speaking_team(db, RULES, mike, alliance)).team_id == lead.id, "of Mike's two, the one whose role speaks"
    await service.remove(db, RULES, alliance, lead.id, member.id)
    with pytest.raises(service.NotFound):
        await service.speaking_team(db, RULES, zed, alliance)  # an outsider cannot tell it is there
    await join(db, alliance, lead, zeds)
    with pytest.raises(service.Forbidden):
        await service.speaking_team(db, RULES, zed, alliance)  # a member's role may not
    row = await relating.apply(db, Relations(), Change(Ref("alliance", alliance.id), Ref("team", rivals.id), score=-60))
    assert (row.subject_kind, row.object_kind, row.score) == ("alliance", "team", -60)
    await relating.apply(db, Relations(), Change(Ref("team", rivals.id), Ref("alliance", alliance.id), score=30))
    assert (await relating.list_for(db, Relations(), Ref("alliance", alliance.id)))[0]["name"] == "Rivals"
    assert (await relating.list_for(db, Relations(), Ref("team", rivals.id)))[0]["name"] == "Pact", "teams can have views of alliances"


# --- the calls ------------------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path, request):
    """The game the app serves: the default alliance rules, or the ones a test parametrizes in (``indirect``)."""
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    alliances = getattr(request, "param", None)
    return Game(name="Test Game", seed_dir=seed_dir, **({"alliances": alliances} if alliances else {}))


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    return login(client, username)


def login(client, username):
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def make_team(client, headers, name):
    return testing.make_team(client, headers, name, heroes=(f"{name} hero"[:24],))["id"]


@pytest.fixture
def world(app_client):
    """Mike's Vanguard and Scouts, Zed's Rivals, and Yan's Strangers: Mike and Zed are signed up, and Mike is logged in."""
    mike = sign_in(app_client, "Mike")
    vanguard, scouts = make_team(app_client, mike, "Vanguard"), make_team(app_client, mike, "Scouts")
    zed = sign_in(app_client, "Zed")
    rivals = make_team(app_client, zed, "Rivals")
    yan = sign_in(app_client, "Yan")
    strangers = make_team(app_client, yan, "Strangers")
    return app_client, {"vanguard": vanguard, "scouts": scouts, "rivals": rivals, "strangers": strangers}


def post(client, who, url, body, status=200):
    return expect(client.post(url, json=body, headers=login(client, who)), status).json()


def test_an_alliance_is_founded_joined_and_run_over_the_calls(world):
    client, ids = world
    found = post(client, "Mike", "/api/alliances", {"team_id": ids["vanguard"], "name": "Iron Pact"}, 201)
    alliance = found["id"]
    assert found["roles"] == ["commander", "subcommander", "treasurer", "quartermaster", "diplomat", "recruiter", "member"]
    assert found["you"] == {"team_id": ids["vanguard"], "role": "commander"} and "appoint" in found["can"] and found["appointable"] == found["roles"][1:]
    post(client, "Mike", f"/api/alliances/{alliance}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"]})
    login(client, "Zed")
    waiting = expect(client.get(f"/api/teams/{ids['rivals']}/invitations"), 200).json()
    assert [row["alliance"] for row in waiting] == ["Iron Pact"]
    joined = post(client, "Zed", f"/api/teams/{ids['rivals']}/invitations/accept", {"alliance_id": alliance})
    assert [(row["team"], row["role"]) for row in joined["members"]] == [("Vanguard", "commander"), ("Rivals", "member")] and joined["can"] == ["vote"]
    assert joined["appointable"] == [] and [row["appoint"] for row in joined["members"]] == [False, False]
    post(client, "Zed", f"/api/alliances/{alliance}/invite", {"team_id": ids["rivals"], "target_team_id": ids["scouts"]}, 403)  # a member
    post(client, "Mike", f"/api/alliances/{alliance}/appoint", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"], "role": "recruiter"})
    post(client, "Zed", f"/api/alliances/{alliance}/invite", {"team_id": ids["rivals"], "target_team_id": ids["scouts"]})  # now a recruiter
    listing = expect(client.get("/api/alliances"), 200).json()
    assert [(row["name"], row["team"], row["role"]) for row in listing["alliances"]] == [("Iron Pact", "Rivals", "recruiter")]
    post(client, "Mike", f"/api/alliances/{alliance}/hand-over", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"]})
    post(client, "Mike", f"/api/alliances/{alliance}/leave", {"team_id": ids["vanguard"]})
    assert post(client, "Zed", f"/api/alliances/{alliance}/disband", {"team_id": ids["rivals"]}) == {"disbanded": True}
    expect(client.get(f"/api/alliances/{alliance}"), 404)


def test_an_outsider_cannot_tell_an_alliance_exists_and_acts_only_for_its_own_teams(world):
    client, ids = world
    alliance = post(client, "Mike", "/api/alliances", {"team_id": ids["vanguard"], "name": "Iron Pact"}, 201)["id"]
    login(client, "Yan")
    expect(client.get(f"/api/alliances/{alliance}"), 404)
    expect(client.get(f"/api/alliances/{alliance}/relationships"), 404)
    post(client, "Yan", f"/api/alliances/{alliance}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["strangers"]}, 404)  # not Yan's team
    post(client, "Yan", f"/api/alliances/{alliance}/invite", {"team_id": ids["strangers"], "target_team_id": ids["strangers"]}, 403)
    post(client, "Yan", f"/api/alliances/{alliance}/relationships/set", {"id": ids["rivals"], "score": 5}, 404)
    post(client, "Mike", f"/api/teams/{ids['strangers']}/invitations/accept", {"alliance_id": alliance}, 404)  # not Mike's team


def test_an_alliance_is_read_as_the_member_team_asked_for(world):
    client, ids = world
    alliance = post(client, "Mike", "/api/alliances", {"team_id": ids["vanguard"], "name": "Iron Pact"}, 201)["id"]
    post(client, "Mike", f"/api/alliances/{alliance}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["scouts"]})
    post(client, "Mike", f"/api/teams/{ids['scouts']}/invitations/accept", {"alliance_id": alliance})
    login(client, "Mike")
    assert expect(client.get(f"/api/alliances/{alliance}"), 200).json()["you"]["team_id"] == ids["vanguard"]  # the first, as before
    scouts = expect(client.get(f"/api/alliances/{alliance}?team_id={ids['scouts']}"), 200).json()
    assert scouts["you"] == {"team_id": ids["scouts"], "role": "member"} and scouts["can"] == ["vote"]
    expect(client.get(f"/api/alliances/{alliance}?team_id={ids['rivals']}"), 404)  # not Mike's team
    login(client, "Zed")
    expect(client.get(f"/api/alliances/{alliance}?team_id={ids['rivals']}"), 404)  # Zed's team, but not in it


def test_the_alliances_own_relationships_are_set_by_a_role_that_speaks_and_read_by_members(world):
    client, ids = world
    alliance = post(client, "Mike", "/api/alliances", {"team_id": ids["vanguard"], "name": "Iron Pact"}, 201)["id"]
    post(client, "Mike", f"/api/alliances/{alliance}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"]})
    post(client, "Zed", f"/api/teams/{ids['rivals']}/invitations/accept", {"alliance_id": alliance})
    url = f"/api/alliances/{alliance}/relationships"
    answer = post(client, "Mike", f"{url}/set", {"id": ids["strangers"], "score": -80, "note": "raiders"})
    assert (answer["name"], answer["band"], answer["note"]) == ("Strangers", "enemy", "raiders")
    post(client, "Zed", f"{url}/set", {"id": ids["strangers"], "score": 50}, 403)  # a member may read but not speak
    read = expect(client.get(url), 200).json()  # (the client is logged in as Zed, a member)
    assert [(row["name"], row["score"], row["note"]) for row in read["relationships"]] == [("Strangers", -80, "raiders")]
    login(client, "Mike")
    assert post(client, "Mike", f"{url}/forget", {"id": ids["strangers"]}) == {"forgotten": True}
    # a team may have a view of the alliance too, through its own calls
    answer = post(client, "Mike", f"/api/teams/{ids['vanguard']}/relationships/set", {"kind": "alliance", "id": alliance, "score": 90})
    assert answer["name"] == "Iron Pact"


def test_the_calls_take_only_what_they_define_and_are_rate_limited(world, monkeypatch):
    client, ids = world
    mike = login(client, "Mike")
    for url, body in (
        ("/api/alliances", {"team_id": ids["vanguard"]}),
        ("/api/alliances", {"team_id": 0, "name": "x"}),
        ("/api/alliances", {"team_id": ids["vanguard"], "name": "x", "motto": "no"}),
        ("/api/alliances", {"team_id": ids["vanguard"], "name": "x" * 65}),
        ("/api/alliances/1/appoint", {"team_id": 1, "target_team_id": 2}),
        ("/api/alliances/1/roles", {"team_id": 1, "name": "x", "actions": "all"}),
        ("/api/alliances/1/leave", {}),
    ):
        expect(client.post(url, json=body, headers=mike), 422)
    expect(client.post("/api/alliances", json={"team_id": ids["vanguard"], "name": "Pact"}), 403)  # no CSRF token
    monkeypatch.setattr(ratelimit, "ALLIANCE_BY_ACCOUNT", ratelimit.Limit("alliance", 1, 3600))
    expect(client.post("/api/alliances", json={"team_id": ids["vanguard"], "name": "Pact"}, headers=mike), 201)
    expect(client.post("/api/alliances", json={"team_id": ids["scouts"], "name": "Pact Two"}, headers=mike), 429)


class Guild(Alliances):
    commander_role, subcommander_role = "master", "warden"
    default_role = "knight"
    starting_roles = {"knight": {"speak"}}


@pytest.mark.parametrize("game", [Guild()], indirect=True)
def test_the_games_roles_are_what_the_calls_use(world):
    client, ids = world
    found = post(client, "Mike", "/api/alliances", {"team_id": ids["vanguard"], "name": "Order"}, 201)
    assert found["roles"] == ["master", "warden", "knight"] and found["you"]["role"] == "master"
    post(client, "Mike", f"/api/alliances/{found['id']}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"]})
    joined = post(client, "Zed", f"/api/teams/{ids['rivals']}/invitations/accept", {"alliance_id": found["id"]})
    assert joined["you"]["role"] == "knight" and joined["can"] == ["speak"]


def test_roles_are_defined_appointed_revoked_and_removed_over_the_calls(world):
    client, ids = world
    mike_headers = login(client, "Mike")

    def send(headers, url, body, status=200):
        return expect(client.post(url, json=body, headers=headers), status).json()

    alliance = send(mike_headers, "/api/alliances", {"team_id": ids["vanguard"], "name": "Iron Pact"}, 201)["id"]
    send(mike_headers, f"/api/alliances/{alliance}/invite", {"team_id": ids["vanguard"], "target_team_id": ids["rivals"]})
    zed_headers = login(client, "Zed")
    send(zed_headers, f"/api/teams/{ids['rivals']}/invitations/accept", {"alliance_id": alliance})
    url = f"/api/alliances/{alliance}"
    mike, zed = {"team_id": ids["vanguard"]}, {"team_id": ids["rivals"]}
    send(zed_headers, f"{url}/roles", {**zed, "name": "Scribe", "actions": ["speak"]}, 403)  # a member can't

    mike_headers = login(client, "Mike")
    made = send(mike_headers, f"{url}/roles", {**mike, "name": "Scribe", "actions": ["speak", "vote"]})
    assert made["role_actions"]["Scribe"] == ["speak", "vote"] and "Scribe" in made["roles"]
    send(mike_headers, f"{url}/roles", {**mike, "name": "Scribe", "actions": ["fly"]}, 409)
    send(mike_headers, f"{url}/appoint", {**mike, "target_team_id": ids["rivals"], "role": "Scribe"})
    send(mike_headers, f"{url}/appoint", {**mike, "target_team_id": ids["rivals"], "role": "captain"}, 404)
    zed_headers = login(client, "Zed")
    assert send(zed_headers, f"{url}/relationships/set", {"id": ids["strangers"], "score": 5})["score"] == 5, "a Scribe speaks for the alliance"

    mike_headers = login(client, "Mike")
    revoked = send(mike_headers, f"{url}/revoke", {**mike, "target_team_id": ids["rivals"]})
    assert [(row["team"], row["role"]) for row in revoked["members"]] == [("Vanguard", "commander"), ("Rivals", "member")]
    send(mike_headers, f"{url}/revoke", {**mike, "target_team_id": ids["rivals"]}, 409)
    send(mike_headers, f"{url}/appoint", {**mike, "target_team_id": ids["rivals"], "role": "Scribe"})
    removed = send(mike_headers, f"{url}/roles/delete", {**mike, "name": "Scribe"})
    assert "Scribe" not in removed["roles"] and removed["members"][1]["role"] == "member"
    send(mike_headers, f"{url}/roles/delete", {**mike, "name": "member"}, 409)
    send(mike_headers, f"{url}/roles/delete", {**mike, "name": "Scribe"}, 404)
