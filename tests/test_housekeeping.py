"""Housekeeping: the jobs that tidy up what has gone stale, and the timer that runs them, on every database."""

import time

from sqlalchemy import select

from terraforma import housekeeping, wallclock
from terraforma.accounts import ratelimit
from terraforma.app import housekeeping_timer
from terraforma.models import RateLimitHit
from terraforma.settings import Settings
from terraforma.testing import in_app_db

HOUR = 60 * 60


async def add_hit(db, limit, subject, window_start):
    db.add(RateLimitHit(bucket=limit.name, subject=subject, window_start=window_start, hits=1))


async def remaining(db):
    return sorted((hit.bucket, hit.subject) for hit in (await db.scalars(select(RateLimitHit))).all())


def run_pass(client):
    async def go():
        return await housekeeping.run(client.app.state.sessionmaker)

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
    assert removed == {"finished_rate_limit_windows": 2}
    assert in_app_db(app_client, remaining) == [("login-ip", "running-15"), ("trade", "running-60")]
    assert run_pass(app_client) == {"finished_rate_limit_windows": 0}, "nothing left to do the second time"


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
    async def broken(session, now):
        raise RuntimeError("boom")

    async def fine(session, now):
        return 3

    monkeypatch.setattr(housekeeping, "JOBS", {"broken": broken, "fine": fine})
    assert run_pass(app_client) == {"fine": 3}
    assert "housekeeping job broken failed" in caplog.text


def test_the_timer_runs_the_pass_again_and_again(app_client, later, monkeypatch):
    passes = []

    async def counting(session, now):
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
