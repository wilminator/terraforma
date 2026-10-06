"""Ballots in alliances: opening, voting (public and secret), closing by time, by turnout or by a role, and the game's say."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account
from terraforma.alliances import ballots, service
from terraforma.alliances.hooks import Alliances
from terraforma.alliances.models import Alliance, Ballot, BallotVote, BallotVoter
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect

anyio = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}
RULES = Alliances()
OPTIONS = ["Yes", "No"]


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the first account)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def alliance_of(db, rules=RULES, members=2):
    """An alliance with its leader (Lead) and ``members`` more teams (T1, T2, ...), one officer among them if there are two or more."""
    mike = await account(db)
    lead = await heroes.create_team(db, mike, "Lead")
    alliance = await service.found(db, rules, lead, "Pact")
    others = []
    for number in range(1, members + 1):
        team = await heroes.create_team(db, mike, f"T{number}")
        await service.invite(db, rules, alliance, lead.id, team.id)
        await service.accept(db, rules, team, alliance.id)
        others.append(team)
    return mike, alliance, lead, others


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


# --- opening -----------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_role_that_may_opens_a_ballot_and_members_may_not(db):
    _mike, alliance, lead, (officer, member) = await alliance_of(db)
    await service.set_role(db, RULES, alliance, lead.id, officer.id, "officer")
    ballot = await ballots.open_ballot(db, RULES, alliance, officer.id, "  Take   the   ford? ", ["Yes", "No"])
    assert (ballot.title, ballot.options, ballot.secret, ballot.closes_at) == ("Take the ford?", ["Yes", "No"], False, None)
    with pytest.raises(service.Forbidden, match="role can't"):
        await ballots.open_ballot(db, RULES, alliance, member.id, "Mine?", OPTIONS)


@anyio
async def test_what_a_ballot_must_look_like(db):
    _mike, alliance, lead, _others = await alliance_of(db)
    bad = [
        ("", OPTIONS), ("x" * 121, OPTIONS), ("Q", ["Only"]), ("Q", [str(n) for n in range(11)]), ("Q", ["Yes", "yes"]),
        ("Q", ["Yes", ""]), ("Q", ["Yes", "n" * 61]),
    ]
    for title, options in bad:
        with pytest.raises(ballots.BallotError):
            await ballots.open_ballot(db, RULES, alliance, lead.id, title, options)
    with pytest.raises(ballots.BallotError, match="kind"):
        await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS, kind="k" * 33)
    with pytest.raises(ballots.BallotError, match="payload"):
        await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS, payload={"x": "y" * 3000})
    for seconds in (0, 59, 30 * 24 * 3600 + 1):
        with pytest.raises(ballots.BallotError, match="stays open"):
            await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS, closes_in=seconds)
    assert await count(db, Ballot) == 0


@anyio
async def test_only_so_many_ballots_are_open_at_once(db):
    _mike, alliance, lead, _others = await alliance_of(db)

    class Few(Alliances):
        max_open_ballots = 2

    first = await ballots.open_ballot(db, Few(), alliance, lead.id, "One", OPTIONS)
    await ballots.open_ballot(db, Few(), alliance, lead.id, "Two", OPTIONS)
    with pytest.raises(ballots.BallotError, match="at most 2"):
        await ballots.open_ballot(db, Few(), alliance, lead.id, "Three", OPTIONS)
    await ballots.close(db, Few(), alliance, lead.id, first)
    await ballots.open_ballot(db, Few(), alliance, lead.id, "Three", OPTIONS)


# --- voting ---------------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_public_ballot_shows_who_voted_for_what_and_a_vote_may_change_until_it_closes(db):
    _mike, alliance, lead, (one, two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Ford?", ["Yes", "No", "Wait"])
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await ballots.cast(db, RULES, alliance, two.id, ballot, 1)
    shown = await ballots.view(db, RULES, alliance, ballot, one.id)
    assert (shown["turnout"], shown["totals"], shown["you_voted"], shown["your_option"], shown["closed"]) == (2, [1, 1, 0], True, 0, False)
    assert [(vote["team"], vote["option"]) for vote in shown["votes"]] == [("T1", 0), ("T2", 1)]
    await ballots.cast(db, RULES, alliance, two.id, ballot, 0)  # changed its mind
    assert (await ballots.view(db, RULES, alliance, ballot, lead.id))["totals"] == [2, 0, 0]
    assert await count(db, BallotVoter) == 2 and await count(db, BallotVote) == 2, "a changed vote is not a second vote"
    assert (await ballots.view(db, RULES, alliance, ballot, lead.id))["you_voted"] is False


@anyio
async def test_a_secret_ballot_hides_what_was_voted_until_it_closes_and_votes_are_final(db):
    _mike, alliance, lead, (one, two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Who leads?", ["Lead", "T1"], secret=True)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 1)
    shown = await ballots.view(db, RULES, alliance, ballot, one.id)
    assert (shown["turnout"], shown["you_voted"]) == (1, True)
    assert "totals" not in shown and "votes" not in shown and "your_option" not in shown
    assert [vote.team_id for vote in (await db.scalars(select(BallotVote))).all()] == [None], "the stored vote has no team on it"
    assert [voter.team_id for voter in (await db.scalars(select(BallotVoter))).all()] == [one.id], "who voted is kept apart"
    with pytest.raises(ballots.BallotError, match="final"):
        await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await ballots.cast(db, RULES, alliance, two.id, ballot, 1)
    await ballots.cast(db, RULES, alliance, lead.id, ballot, 0)  # everyone has voted: it closes
    done = await ballots.view(db, RULES, alliance, ballot, one.id)
    assert done["closed"] and done["totals"] == [1, 2] and done["result"]["winner"] == 1 and "votes" not in done


@anyio
async def test_who_may_vote_and_for_what(db):
    _mike, alliance, lead, (one, _two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS)
    for option in (-1, 2):
        with pytest.raises(ballots.BallotError, match="no such option"):
            await ballots.cast(db, RULES, alliance, one.id, ballot, option)
    with pytest.raises(service.Forbidden, match="not in the alliance"):
        await ballots.cast(db, RULES, alliance, 999_999, ballot, 0)

    class NoVoting(Alliances):
        permissions = {**Alliances.permissions, "member": set()}

    with pytest.raises(service.Forbidden, match="can't vote"):
        await ballots.cast(db, NoVoting(), alliance, one.id, ballot, 0)


# --- closing and counting --------------------------------------------------------------------------------------------------------

@anyio
async def test_a_ballot_closes_when_everyone_who_may_vote_has_and_plurality_wins(db):
    _mike, alliance, lead, (one, two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", ["A", "B", "C"])
    await ballots.cast(db, RULES, alliance, lead.id, ballot, 2)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 2)
    assert ballot.closed_at is None
    await ballots.cast(db, RULES, alliance, two.id, ballot, 0)
    assert ballot.closed_at is not None and ballot.result == {"winner": 2, "totals": [1, 0, 2], "turnout": 3, "eligible": 3}
    with pytest.raises(ballots.BallotError, match="closed"):
        await ballots.cast(db, RULES, alliance, two.id, ballot, 1)


@anyio
async def test_a_tie_or_no_votes_decides_nothing(db):
    _mike, alliance, lead, (one, _two) = await alliance_of(db)
    tied = await ballots.open_ballot(db, RULES, alliance, lead.id, "Tie", OPTIONS)
    await ballots.cast(db, RULES, alliance, lead.id, tied, 0)
    await ballots.cast(db, RULES, alliance, one.id, tied, 1)
    await ballots.close(db, RULES, alliance, lead.id, tied)
    assert tied.result["winner"] is None and tied.result["totals"] == [1, 1]
    empty = await ballots.open_ballot(db, RULES, alliance, lead.id, "Empty", OPTIONS)
    await ballots.close(db, RULES, alliance, lead.id, empty)
    assert empty.result == {"winner": None, "totals": [0, 0], "turnout": 0, "eligible": 3}


@anyio
async def test_only_a_role_that_may_closes_a_ballot_early_and_not_twice(db):
    _mike, alliance, lead, (one, _two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS)
    with pytest.raises(service.Forbidden):
        await ballots.close(db, RULES, alliance, one.id, ballot)
    await ballots.close(db, RULES, alliance, lead.id, ballot)
    with pytest.raises(ballots.BallotError, match="closed"):
        await ballots.close(db, RULES, alliance, lead.id, ballot)


@anyio
async def test_a_ballot_closes_when_its_time_is_up_the_next_time_anyone_looks(db, later):
    _mike, alliance, lead, (one, _two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS, closes_in=3600)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 1)
    later(3599)
    assert (await ballots.view(db, RULES, alliance, ballot, lead.id))["closed"] is False
    later(3600)
    shown = await ballots.view(db, RULES, alliance, ballot, lead.id)
    assert shown["closed"] and shown["result"]["winner"] == 1 and shown["result"]["turnout"] == 1
    with pytest.raises(ballots.BallotError, match="closed"):
        await ballots.cast(db, RULES, alliance, lead.id, ballot, 0)


@anyio
async def test_close_due_closes_every_ballot_that_is_due_for_a_games_own_schedule(db, later):
    _mike, alliance, lead, _others = await alliance_of(db)
    soon = await ballots.open_ballot(db, RULES, alliance, lead.id, "Soon", OPTIONS, closes_in=60)
    later_one = await ballots.open_ballot(db, RULES, alliance, lead.id, "Later", OPTIONS, closes_in=7200)
    never = await ballots.open_ballot(db, RULES, alliance, lead.id, "Never", OPTIONS)
    later(120)
    assert await ballots.close_due(db, lambda each: RULES) == 1
    assert (soon.closed_at is not None, later_one.closed_at, never.closed_at) == (True, None, None)
    later(7200)
    assert await ballots.close_due(db, lambda each: RULES) == 1 and await ballots.close_due(db, lambda each: RULES) == 0


@anyio
async def test_the_game_weighs_votes_and_decides_who_wins(db):
    class Weighted(Alliances):
        async def vote_weight(self, session, alliance, member):
            return {"leader": 3, "officer": 2}.get(member.role, 1)

        async def decide(self, session, alliance, ballot, totals, eligible):
            top = max(totals)
            return totals.index(top) if top * 2 > eligible else None  # a majority of all the weight, or nothing

    rules = Weighted()
    _mike, alliance, lead, (officer, member) = await alliance_of(db, rules)
    await service.set_role(db, rules, alliance, lead.id, officer.id, "officer")
    ballot = await ballots.open_ballot(db, rules, alliance, lead.id, "Q", OPTIONS)
    await ballots.cast(db, rules, alliance, officer.id, ballot, 0)  # 2
    await ballots.cast(db, rules, alliance, member.id, ballot, 1)  # 1
    await ballots.close(db, rules, alliance, lead.id, ballot)
    assert ballot.result == {"winner": None, "totals": [2, 1], "turnout": 2, "eligible": 3}, "2 of 6 weight is not a majority"
    second = await ballots.open_ballot(db, rules, alliance, lead.id, "Q2", OPTIONS)
    await ballots.cast(db, rules, alliance, lead.id, second, 1)  # 3
    await ballots.cast(db, rules, alliance, member.id, second, 1)  # 1
    await ballots.cast(db, rules, alliance, officer.id, second, 0)  # 2: all voted
    assert second.result == {"winner": 1, "totals": [2, 4], "turnout": 3, "eligible": 3}


@anyio
async def test_the_game_acts_on_a_closed_ballot_from_its_kind_and_payload(db):
    heard = []

    class Acts(Alliances):
        async def on_ballot_closed(self, session, alliance, ballot, result):
            heard.append((ballot.kind, ballot.payload, result["winner"]))
            if ballot.kind == "remove" and result["winner"] == 0:
                await service.remove(session, self, alliance, ballot.opened_by_team_id, ballot.payload["team_id"])

    rules = Acts()
    _mike, alliance, lead, (one, two) = await alliance_of(db, rules)
    ballot = await ballots.open_ballot(db, rules, alliance, lead.id, "Remove T2?", ["Yes", "No"], kind="remove", payload={"team_id": two.id})
    for team in (lead, one, two):
        await ballots.cast(db, rules, alliance, team.id, ballot, 0)
    assert heard == [("remove", {"team_id": two.id}, 0)]
    assert {row.team_id for row in await service.members(db, alliance.id)} == {lead.id, one.id}


@anyio
async def test_a_game_that_names_an_option_that_is_not_there_is_refused(db):
    class Broken(Alliances):
        async def decide(self, session, alliance, ballot, totals, eligible):
            return 7

    _mike, alliance, lead, _others = await alliance_of(db, Broken())
    ballot = await ballots.open_ballot(db, Broken(), alliance, lead.id, "Q", OPTIONS)
    with pytest.raises(ballots.BallotError, match="not on the ballot"):
        await ballots.close(db, Broken(), alliance, lead.id, ballot)


# --- when teams go ----------------------------------------------------------------------------------------------------------------

@anyio
async def test_a_team_that_leaves_takes_its_open_votes_with_it_and_a_deleted_team_leaves_no_trace(db):
    mike, alliance, lead, (one, two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await ballots.cast(db, RULES, alliance, two.id, ballot, 1)
    await service.leave(db, RULES, alliance, one.id)
    assert (await ballots.view(db, RULES, alliance, ballot, lead.id))["totals"] == [0, 1]
    await heroes.delete_team(db, mike, two.id)
    assert await count(db, BallotVote) == 0 and await count(db, BallotVoter) == 0


@anyio
async def test_disbanding_takes_the_ballots_with_it(db):
    _mike, alliance, lead, (one, _two) = await alliance_of(db)
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", OPTIONS)
    await ballots.cast(db, RULES, alliance, one.id, ballot, 0)
    await service.disband(db, RULES, alliance, lead.id)
    assert (await count(db, Ballot), await count(db, BallotVote), await count(db, BallotVoter)) == (0, 0, 0)


# --- the calls ------------------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path, request):
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


def post(client, who, url, body, status=200):
    return expect(client.post(url, json=body, headers=login(client, who)), status).json()


@pytest.fixture
def pact(app_client):
    """Mike's Vanguard leads Iron Pact; Zed's Rivals is a member; Yan's Strangers is not in it."""
    mike = sign_in(app_client, "Mike")
    vanguard = testing.make_team(app_client, mike, "Vanguard")["id"]
    zed = sign_in(app_client, "Zed")
    rivals = testing.make_team(app_client, zed, "Rivals")["id"]
    yan = sign_in(app_client, "Yan")
    strangers = testing.make_team(app_client, yan, "Strangers")["id"]
    alliance = post(app_client, "Mike", "/api/alliances", {"team_id": vanguard, "name": "Iron Pact"}, 201)["id"]
    post(app_client, "Mike", f"/api/alliances/{alliance}/invite", {"team_id": vanguard, "target_team_id": rivals})
    post(app_client, "Zed", f"/api/teams/{rivals}/invitations/accept", {"alliance_id": alliance})
    return app_client, {"alliance": alliance, "vanguard": vanguard, "rivals": rivals, "strangers": strangers}


def test_a_ballot_is_opened_voted_on_and_closed_over_the_calls(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/ballots"
    opened = post(client, "Mike", url, {"team_id": ids["vanguard"], "title": "Take the ford?", "options": ["Yes", "No"], "kind": "motion", "payload": {"n": 1}}, 201)
    assert (opened["title"], opened["options"], opened["secret"], opened["closed"], opened["payload"]) == ("Take the ford?", ["Yes", "No"], False, False, {"n": 1})
    ballot = opened["id"]
    voted = post(client, "Zed", f"{url}/{ballot}/vote", {"team_id": ids["rivals"], "option": 1})
    assert (voted["you_voted"], voted["your_option"], voted["totals"], voted["closed"]) == (True, 1, [0, 1], False)
    shown = expect(client.get(f"{url}/{ballot}"), 200).json()  # Zed is logged in
    assert [(vote["team"], vote["option"]) for vote in shown["votes"]] == [("Rivals", 1)]
    done = post(client, "Mike", f"{url}/{ballot}/vote", {"team_id": ids["vanguard"], "option": 0})
    assert done["closed"] and done["result"] == {"winner": None, "totals": [1, 1], "turnout": 2, "eligible": 2}
    listing = expect(client.get(url), 200).json()
    assert [row["title"] for row in listing] == ["Take the ford?"]


def test_a_secret_ballot_over_the_calls_shows_only_turnout_until_it_closes(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/ballots"
    ballot = post(client, "Mike", url, {"team_id": ids["vanguard"], "title": "Who leads?", "options": ["Vanguard", "Rivals"], "secret": True}, 201)["id"]
    seen = post(client, "Zed", f"{url}/{ballot}/vote", {"team_id": ids["rivals"], "option": 1})
    assert seen["turnout"] == 1 and seen["you_voted"] and "totals" not in seen and "votes" not in seen
    post(client, "Zed", f"{url}/{ballot}/vote", {"team_id": ids["rivals"], "option": 0}, 409)  # final
    post(client, "Mike", f"{url}/{ballot}/close", {"team_id": ids["vanguard"]})
    closed = expect(client.get(f"{url}/{ballot}"), 200).json()
    assert closed["totals"] == [0, 1] and closed["result"]["winner"] == 1 and "votes" not in closed


def test_outsiders_members_and_strangers_see_what_they_may(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/ballots"
    ballot = post(client, "Mike", url, {"team_id": ids["vanguard"], "title": "Q", "options": ["Yes", "No"]}, 201)["id"]
    post(client, "Zed", url, {"team_id": ids["rivals"], "title": "Mine", "options": ["Yes", "No"]}, 403)  # a member may not open
    post(client, "Zed", f"{url}/{ballot}/close", {"team_id": ids["rivals"]}, 403)
    login(client, "Yan")
    expect(client.get(url), 404)
    expect(client.get(f"{url}/{ballot}"), 404)
    post(client, "Yan", f"{url}/{ballot}/vote", {"team_id": ids["strangers"], "option": 0}, 403)  # not in it, and the ballot is not named
    post(client, "Yan", f"{url}/{ballot}/vote", {"team_id": ids["rivals"], "option": 0}, 404)  # not Yan's team
    login(client, "Zed")
    expect(client.get(f"{url}/9999"), 404)


def test_the_calls_take_only_what_they_define_and_are_rate_limited(pact, monkeypatch):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/ballots"
    mike = login(client, "Mike")
    ok = {"team_id": ids["vanguard"], "title": "Q", "options": ["Yes", "No"]}
    for body in (
        {**ok, "options": ["Only"]}, {**ok, "options": []}, {**ok, "title": ""}, {**ok, "secret": "yes"}, {**ok, "closes_in_hours": 0},
        {**ok, "closes_in_hours": 721}, {**ok, "extra": 1}, {"team_id": ids["vanguard"], "options": ["A", "B"]},
    ):
        expect(client.post(url, json=body, headers=mike), 422)
    expect(client.post(f"{url}/1/vote", json={"team_id": ids["vanguard"], "option": -1}, headers=mike), 422)
    expect(client.post(url, json=ok), 403)  # no CSRF token
    monkeypatch.setattr(ratelimit, "ALLIANCE_BY_ACCOUNT", ratelimit.Limit("alliance-small", 1, 3600))
    expect(client.post(url, json=ok, headers=mike), 201)
    expect(client.post(url, json=ok, headers=mike), 429)


class Council(Alliances):
    option_limits = (2, 3)
    close_when_all_voted = False


@pytest.mark.parametrize("game", [Council()], indirect=True)
def test_the_games_rules_are_what_the_calls_use(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}/ballots"
    post(client, "Mike", url, {"team_id": ids["vanguard"], "title": "Q", "options": ["A", "B", "C", "D"]}, 409)  # at most 3 here
    ballot = post(client, "Mike", url, {"team_id": ids["vanguard"], "title": "Q", "options": ["A", "B", "C"]}, 201)["id"]
    post(client, "Mike", f"{url}/{ballot}/vote", {"team_id": ids["vanguard"], "option": 0})
    still_open = post(client, "Zed", f"{url}/{ballot}/vote", {"team_id": ids["rivals"], "option": 0})
    assert still_open["closed"] is False, "this game does not close a ballot when everyone has voted"
