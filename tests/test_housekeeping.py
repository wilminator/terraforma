"""Housekeeping: the jobs that tidy up what has gone stale, and the timer that runs them, on every database."""

import time
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import housekeeping, wallclock
from terraforma.accounts import ratelimit
from terraforma.app import housekeeping_timer
from terraforma.alliances import ballots
from terraforma.alliances.models import Ballot, BallotVote, BallotVoter
from terraforma.models import RateLimitHit
from terraforma.relations.models import ANSWERED, DISMISSED, PENDING, RatingPrompt
from terraforma.settings import Settings
from terraforma.testing import in_app_db

from .test_ballots import RULES, alliance_of
from .test_ratings import a_pvp_fight

HOUR = 60 * 60
DAY = 24 * HOUR


async def add_hit(db, limit, subject, window_start):
    db.add(RateLimitHit(bucket=limit.name, subject=subject, window_start=window_start, hits=1))


async def remaining(db):
    return sorted((hit.bucket, hit.subject) for hit in (await db.scalars(select(RateLimitHit))).all())


def run_pass(client):
    async def go():
        return await housekeeping.run(client.app.state.sessionmaker, client.app.state.settings)

    return client.portal.call(go)


# --- the rate-limit job -----------------------------------------------------------------------------------------------------------

def test_finished_rate_limit_windows_go_and_the_running_ones_stay(app_client, later):
    later(0)
    now = wallclock.timestamp()

    async def fill(db):
        # One window of each length, one over and one still running.
        await add_hit(db, ratelimit.LOGIN_BY_ADDRESS, "over-15", now - ratelimit.LOGIN_BY_ADDRESS.window)
        await add_hit(db, ratelimit.LOGIN_BY_ADDRESS, "running-15", now - ratelimit.LOGIN_BY_ADDRESS.window + 1)
        await add_hit(db, ratelimit.TRADE_BY_ACCOUNT, "over-60", now - 6 * HOUR)
        await add_hit(db, ratelimit.TRADE_BY_ACCOUNT, "running-60", now - HOUR + 1)

    in_app_db(app_client, fill)
    removed = run_pass(app_client)
    assert removed == {"finished_rate_limit_windows": 2, "stale_pending_drops": 0}
    assert in_app_db(app_client, remaining) == [("login-ip", "running-15"), ("trade", "running-60")]
    assert run_pass(app_client) == {"finished_rate_limit_windows": 0, "stale_pending_drops": 0}, "nothing left to do the second time"
    assert removed["finished_rate_limit_windows"] == 2
    assert in_app_db(app_client, remaining) == [("login-ip", "running-15"), ("trade", "running-60")]
    assert run_pass(app_client)["finished_rate_limit_windows"] == 0, "nothing left to do the second time"


def test_a_one_off_subject_is_tidied_after_its_window(app_client, later):
    """A subject that never comes back keeps its row until housekeeping removes it."""
    later(0)
    app_client.post("/api/login", json={"username": "nobody", "password": "correct horse battery"})
    assert in_app_db(app_client, remaining), "the attempt was counted"
    later(2 * HOUR)
    run_pass(app_client)
    assert in_app_db(app_client, remaining) == []


def test_every_limit_is_known_to_housekeeping_under_its_own_name():
    names = [limit.name for limit in ratelimit.ALL_LIMITS]
    assert len(names) == len(set(names)) > 5
    assert ratelimit.TRADE_BY_ACCOUNT in ratelimit.ALL_LIMITS and ratelimit.LOGIN_BY_NAME in ratelimit.ALL_LIMITS


# --- the pass and the timer -------------------------------------------------------------------------------------------------------

def test_a_failing_job_is_logged_and_the_others_still_run(app_client, later, monkeypatch, caplog):
    async def broken(session, now, settings):
        raise RuntimeError("boom")

    async def fine(session, now, settings):
        return 3

    monkeypatch.setattr(housekeeping, "JOBS", {"broken": broken, "fine": fine})
    assert run_pass(app_client) == {"fine": 3}
    assert "housekeeping job broken failed" in caplog.text


def test_the_timer_runs_the_pass_again_and_again(app_client, later, monkeypatch):
    passes = []

    async def counting(session, now, settings):
        passes.append(now)
        return 0

    monkeypatch.setattr(housekeeping, "JOBS", {"counting": counting})
    timer = app_client.portal.start_task_soon(housekeeping_timer, app_client.app, 0.01)
    try:
        deadline = time.monotonic() + 60
        while len(passes) < 2:
            assert time.monotonic() < deadline, "the timer never ran the jobs twice"
            time.sleep(0.05)
    finally:
        timer.cancel()


def test_housekeeping_is_hourly_by_default_and_the_tests_turn_it_off(app_client):
    assert Settings(session_secret="x" * 32).housekeeping_seconds == 3600
    assert app_client.app.state.settings.housekeeping_seconds == 0


# --- retention: closed ballots and settled rating prompts -------------------------------------------------------------------------

def keeping(**days):
    return Settings(session_secret="x" * 32, **days)


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


async def closed_ballot(db, alliance, lead, others, closed_at):
    ballot = await ballots.open_ballot(db, RULES, alliance, lead.id, "Q", ["A", "B"])
    for team in (lead, *others):
        await ballots.cast(db, RULES, alliance, team.id, ballot, 0)
    ballot.closed_at = closed_at
    return ballot


@pytest.mark.anyio
async def test_old_closed_ballots_go_with_their_votes_and_newer_and_open_ones_stay(db, later):
    later(0)
    now = wallclock.timestamp()
    _mike, alliance, lead, others = await alliance_of(db, members=2)
    old = await closed_ballot(db, alliance, lead, others, now - 31 * DAY)
    recent = await closed_ballot(db, alliance, lead, others, now - 29 * DAY)
    still_open = await ballots.open_ballot(db, RULES, alliance, lead.id, "Open", ["A", "B"])
    await db.flush()
    assert await count(db, BallotVote) == 6 and await count(db, BallotVoter) == 6

    removed = await housekeeping.old_closed_ballots(db, now, keeping(ballot_retention_days=30))
    assert removed == 1
    assert sorted((await db.scalars(select(Ballot.id))).all()) == sorted([recent.id, still_open.id])
    assert await count(db, BallotVote) == 3 and await count(db, BallotVoter) == 3, "only the old ballot's votes went"
    assert old.id not in (await db.scalars(select(BallotVote.ballot_id))).all()


@pytest.mark.anyio
async def test_ballots_are_kept_for_good_unless_a_retention_is_set(db, later):
    later(0)
    now = wallclock.timestamp()
    _mike, alliance, lead, others = await alliance_of(db, members=2)
    await closed_ballot(db, alliance, lead, others, now - 3650 * DAY)
    await db.flush()
    assert await housekeeping.old_closed_ballots(db, now, keeping()) == 0
    assert await count(db, Ballot) == 1


@pytest.mark.anyio
async def test_old_answered_and_dismissed_prompts_go_and_pending_and_newer_ones_stay(db, later):
    later(0)
    _mike, _zed, alpha, bravo, _a, _b, _fight, record = await a_pvp_fight(db)
    old, new = wallclock.now() - timedelta(days=31), wallclock.now() - timedelta(days=29)
    for subject, object, state, touched in (
        (alpha.id, bravo.id, ANSWERED, old),
        (bravo.id, alpha.id, DISMISSED, old),
        (alpha.id, alpha.id, PENDING, old),
        (bravo.id, bravo.id, ANSWERED, new),
    ):
        db.add(RatingPrompt(fight_id=record.id, subject_team_id=subject, object_team_id=object, interaction="harmed", state=state, updated_at=touched))
    await db.flush()

    removed = await housekeeping.old_settled_rating_prompts(db, wallclock.timestamp(), keeping(rating_prompt_retention_days=30))
    assert removed == 2
    left = (await db.scalars(select(RatingPrompt))).all()
    assert sorted((each.state, each.subject_team_id == each.object_team_id) for each in left) == [(ANSWERED, True), (PENDING, True)]


@pytest.mark.anyio
async def test_prompts_are_kept_for_good_unless_a_retention_is_set(db, later):
    later(0)
    _mike, _zed, alpha, bravo, _a, _b, _fight, record = await a_pvp_fight(db)
    db.add(RatingPrompt(fight_id=record.id, subject_team_id=alpha.id, object_team_id=bravo.id, interaction="harmed", state=ANSWERED, updated_at=wallclock.now() - timedelta(days=3650)))
    await db.flush()
    assert await housekeeping.old_settled_rating_prompts(db, wallclock.timestamp(), keeping()) == 0
    assert await count(db, RatingPrompt) == 1


def test_retention_is_off_by_default_and_never_negative():
    settings = Settings(session_secret="x" * 32)
    assert (settings.ballot_retention_days, settings.rating_prompt_retention_days) == (0, 0)
    with pytest.raises(ValueError):
        keeping(ballot_retention_days=-1)
