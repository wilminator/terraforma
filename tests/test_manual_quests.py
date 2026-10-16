"""Manual quests: events an admin writes by hand (a competition, a tournament), published, entered by teams against a clock, judged and cancelled.
On every database."""

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.accounts import ratelimit
from terraforma.challenge import service as challenge
from terraforma.game import Game
from terraforma.models import Account
from terraforma.quests import manual
from terraforma.quests import service as quests
from terraforma.quests.hooks import ManualQuests
from terraforma.quests.manual_models import ManualEntry
from terraforma.testing import in_app_db, make_team

from .helpers import expect
from .test_npc_state import a_party, check, npc_says
from .test_towns import PASSWORD, sign_in
from .test_weekly_quests import SEED, a_team

pytestmark = pytest.mark.anyio

DAY = 24 * 60 * 60


class Hooks(ManualQuests):
    """A game that turns away one team and keeps the penalties it was asked to charge."""

    def __init__(self, refuse_team=None):
        self.refuse_team, self.penalties = refuse_team, []

    async def may_enter(self, session, team_id, quest):
        return "Only friends." if team_id == self.refuse_team else None

    async def penalty(self, session, team_id, quest, reason):
        self.penalties.append((team_id, quest.key, reason))


def fields(key="arena", **over):
    """An event that opens now, closes in a day and ends in three; a team has a day to finish."""
    now = wallclock.timestamp()
    return {"key": key, "name": "Arena", "kind": "coliseum", "description": "Fight.", "spec": {"rounds": 3}, "opens_at": now, "closes_at": now + DAY,
            "ends_at": now + 3 * DAY, "limit_seconds": DAY, **over}


async def a_published(db, key="arena", **over):
    quest = await manual.create(db, None, **fields(key, **over))
    return await manual.publish(db, quest.id)


# --- writing it --------------------------------------------------------------------------------------------------------

async def test_an_event_is_written_as_a_draft_edited_and_published(db):
    quest = await manual.create(db, None, **fields())
    assert (quest.status, quest.kind, quest.spec) == ("draft", "coliseum", {"rounds": 3})
    await manual.update(db, quest.id, name="  Grand   Arena ", spec={"rounds": 5}, max_entries=8)
    assert (quest.name, quest.spec, quest.max_entries) == ("Grand Arena", {"rounds": 5}, 8)
    await manual.publish(db, quest.id)
    assert quest.status == "published"
    with pytest.raises(manual.ManualError, match="only a draft"):
        await manual.update(db, quest.id, name="Other")
    with pytest.raises(manual.ManualError, match="only a draft"):
        await manual.publish(db, quest.id)


@pytest.mark.parametrize("bad, why", [
    ({"key": "Bad Key"}, "key"), ({"name": "  "}, "name"), ({"spec": []}, "spec"), ({"description": "x" * 4001}, "description"),
    ({"kind": "k" * 33}, "kind"), ({"limit_seconds": -1}, "limit_seconds"), ({"closes_at": 0}, "opens before"),
    ({"ends_at": 1}, "opens before"), ({"ends_at": wallclock.timestamp() + 10 * 366 * DAY}, "five years"), ({"map_id": 999}, "no such map"), ({"x": 1}, "needs its map"),
])
async def test_a_bad_event_is_refused(db, bad, why):
    with pytest.raises(manual.ManualError, match=why):
        await manual.create(db, None, **fields(**bad))


async def test_a_key_is_used_once_and_an_event_stands_on_a_tile_of_a_map(db):
    from terraforma.world.start import ensure_start

    await manual.create(db, None, **fields())
    with pytest.raises(manual.ManualError, match="already exists"):
        await manual.create(db, None, **fields())
    hub = await ensure_start(db)
    placed = await manual.create(db, None, **fields("sited", map_id=hub.id, x=0, y=0))
    assert (placed.map_id, placed.x, placed.y) == (hub.id, 0, 0)
    with pytest.raises(manual.ManualError, match="a tile of its map"):
        await manual.create(db, None, **fields("off", map_id=hub.id, x=hub.width, y=0))
    with pytest.raises(manual.ManualError, match="already exists"):
        await manual.update(db, placed.id, key="arena")


async def test_an_event_that_has_already_closed_cannot_be_published(db, later):
    quest = await manual.create(db, None, **fields())
    later(2 * DAY)
    with pytest.raises(manual.ManualError, match="already closed"):
        await manual.publish(db, quest.id)


# --- entering ----------------------------------------------------------------------------------------------------------

async def test_a_team_enters_an_open_published_event_and_its_clock_starts(db):
    team = await a_team(db)
    draft = await manual.create(db, None, **fields("draft"))
    with pytest.raises(manual.ManualError, match="no such event"):
        await manual.enter(db, Hooks(), team.id, draft.id)
    quest = await a_published(db)
    now = wallclock.timestamp()
    entry = await manual.enter(db, Hooks(), team.id, quest.id)
    assert (entry.status, entry.started_at, entry.deadline, entry.score) == ("active", now, now + DAY, 0)
    assert (await manual.enter(db, Hooks(), team.id, quest.id)).id == entry.id, "entering twice is one entry"


async def test_an_event_without_a_limit_runs_until_it_is_over_and_no_entry_outlasts_it(db, later):
    first, second = await a_team(db, "First"), await a_team(db, "Second")
    quest = await a_published(db, limit_seconds=0)
    assert (await manual.enter(db, Hooks(), first.id, quest.id)).deadline == quest.ends_at
    later(DAY - 1)
    assert (await manual.enter(db, Hooks(), second.id, quest.id)).deadline == quest.ends_at
    long = await a_published(db, "long", limit_seconds=10 * DAY)
    assert (await manual.enter(db, Hooks(), first.id, long.id)).deadline == long.ends_at, "never later than the end of the event"


async def test_entering_waits_for_the_opening_stops_at_the_closing_and_stops_when_full(db, later):
    now = wallclock.timestamp()
    team, other = await a_team(db, "First"), await a_team(db, "Second")
    quest = await a_published(db, opens_at=now + 100, max_entries=1)
    with pytest.raises(manual.ManualError, match="not opened"):
        await manual.enter(db, Hooks(), team.id, quest.id)
    later(100)
    await manual.enter(db, Hooks(), team.id, quest.id)
    with pytest.raises(manual.ManualError, match="full"):
        await manual.enter(db, Hooks(), other.id, quest.id)
    later(DAY)
    with pytest.raises(manual.ManualError, match="closed to new entries"):
        await manual.enter(db, Hooks(), other.id, quest.id)


async def test_the_game_may_turn_a_team_away(db):
    team = await a_team(db)
    quest = await a_published(db)
    with pytest.raises(manual.ManualError, match="Only friends"):
        await manual.enter(db, Hooks(refuse_team=team.id), team.id, quest.id)
    assert await db.scalar(select(func.count()).select_from(ManualEntry)) == 0


# --- the clock and the result ------------------------------------------------------------------------------------------

async def test_a_won_entry_is_scored_and_added_to_the_teams_record(db):
    team = await a_team(db)
    quest = await a_published(db)
    entry = await manual.enter(db, Hooks(), team.id, quest.id)
    done = await manual.finish(db, Hooks(), entry.id, True, score=120)
    assert (done.status, done.score, done.finished_at) == ("completed", 120, wallclock.timestamp())
    assert await quests.completed(db, team.id, "event", 0) == 1 and await quests.completed(db, team.id, "arena", 0) == 1
    with pytest.raises(manual.ManualError, match="already completed"):
        await manual.finish(db, Hooks(), entry.id, True)


async def test_a_lost_entry_costs_what_the_game_says(db):
    team = await a_team(db)
    quest = await a_published(db)
    hooks = Hooks()
    entry = await manual.enter(db, hooks, team.id, quest.id)
    done = await manual.finish(db, hooks, entry.id, False, score=5)
    assert (done.status, done.failure, done.score) == ("failed", "poor_performance", 5)
    assert hooks.penalties == [(team.id, "arena", "poor_performance")]
    assert await quests.completed(db, team.id, "event") == 0
    with pytest.raises(manual.ManualError, match="already failed"):
        await manual.finish(db, hooks, entry.id, True)


async def test_an_entry_past_its_deadline_has_failed_with_a_timeout_whatever_the_outcome(db, later):
    team = await a_team(db)
    quest = await a_published(db)
    hooks = Hooks()
    entry = await manual.enter(db, hooks, team.id, quest.id)
    later(DAY)
    await manual.settle(db, hooks, team.id)
    assert entry.status == "active", "the last second is still in time"
    later(DAY + 1)
    late = await manual.finish(db, hooks, entry.id, True, score=99)
    assert (late.status, late.failure, late.score) == ("failed", "timeout", 0)
    assert hooks.penalties == [(team.id, "arena", "timeout")]
    assert await quests.completed(db, team.id, "arena") == 0


async def test_settling_a_team_fails_only_its_late_entries(db, later):
    team, other = await a_team(db, "First"), await a_team(db, "Second")
    quest = await a_published(db)
    hooks = Hooks()
    mine = await manual.enter(db, hooks, team.id, quest.id)
    later(DAY // 2)
    theirs = await manual.enter(db, hooks, other.id, quest.id)
    later(DAY + 1)
    await manual.settle(db, hooks, team.id)
    await db.refresh(theirs)
    assert (mine.status, theirs.status) == ("failed", "active")
    assert hooks.penalties == [(team.id, "arena", "timeout")]


async def test_a_score_is_a_whole_number_in_range_and_a_reason_is_one_the_engine_knows(db):
    team = await a_team(db)
    entry = await manual.enter(db, Hooks(), team.id, (await a_published(db)).id)
    for bad in (-1, manual.MAX_SCORE + 1):
        with pytest.raises(manual.ManualError, match="a score"):
            await manual.finish(db, Hooks(), entry.id, True, score=bad)
    with pytest.raises(manual.ManualError, match="running out of time"):
        await manual.finish(db, Hooks(), entry.id, False, reason="world")
    with pytest.raises(manual.ManualError, match="no such entry"):
        await manual.finish(db, Hooks(), 9999, True)


# --- the standings and cancelling --------------------------------------------------------------------------------------

async def test_the_standings_rank_by_score_and_break_a_tie_by_who_finished_first(db, later):
    teams = [await a_team(db, name) for name in ("Low", "Early", "Late", "Running")]
    quest = await a_published(db)
    made = [await manual.enter(db, Hooks(), team.id, quest.id) for team in teams]
    await manual.finish(db, Hooks(), made[0].id, True, score=10)
    later(60)
    await manual.finish(db, Hooks(), made[1].id, True, score=50)
    later(120)
    await manual.finish(db, Hooks(), made[2].id, True, score=50)
    ranked = await manual.entries(db, quest)
    assert [each.team_id for each in ranked] == [teams[1].id, teams[2].id, teams[0].id, teams[3].id]


async def test_cancelling_ends_the_running_entries_with_no_penalty_and_the_event_disappears(db):
    team, other = await a_team(db, "First"), await a_team(db, "Second")
    quest = await a_published(db)
    hooks = Hooks()
    running = await manual.enter(db, hooks, team.id, quest.id)
    won = await manual.enter(db, hooks, other.id, quest.id)
    await manual.finish(db, hooks, won.id, True, score=1)
    await manual.cancel(db, quest.id)
    await db.refresh(running)
    assert (running.status, running.failure) == ("ended", "cancelled") and hooks.penalties == []
    assert won.status == "completed", "a result already given stands"
    assert await manual.open_now(db) == []
    with pytest.raises(manual.ManualError, match="already cancelled"):
        await manual.cancel(db, quest.id)
    with pytest.raises(manual.ManualError, match="no such event"):
        await manual.enter(db, hooks, team.id, quest.id)


async def test_a_draft_can_be_thrown_away_and_open_events_leave_out_the_over_ones(db, later):
    draft = await manual.create(db, None, **fields("draft"))
    await manual.cancel(db, draft.id)
    shown = await a_published(db, "shown")
    await a_published(db, "over", ends_at=wallclock.timestamp() + DAY, closes_at=wallclock.timestamp() + 10)
    assert [each.key for each in await manual.open_now(db)] == ["shown", "over"]
    later(DAY + 1)
    assert [each.key for each in await manual.open_now(db)] == ["shown"]
    assert [each.key for each in await manual.listing(db, "cancelled")] == ["draft"]
    assert [each.key for each in await manual.listing(db, after=shown.id - 1, limit=1)] == ["shown"]


async def test_a_deleted_team_takes_its_entries_with_it(db):
    team = await a_team(db)
    await manual.enter(db, Hooks(), team.id, (await a_published(db)).id)
    await manual.forget_team(db, team.id)
    assert await db.scalar(select(func.count()).select_from(ManualEntry)) == 0


# --- the calls ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    import json

    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    return Game(name="Test Game", seed_dir=seed_dir, manual_quests=Hooks())


async def _make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


def body(key="arena", **over):
    now = wallclock.timestamp()
    return {"key": key, "name": "Arena", "kind": "coliseum", "spec": {"rounds": 3}, "opens_at": now, "closes_at": now + DAY, "ends_at": now + 3 * DAY,
            "limit_seconds": DAY, **over}


def log_in(client, username):
    """Signs in again as an account that exists (``sign_in`` makes the account)."""
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def an_admin(app_client, name="Root"):
    headers = sign_in(app_client, name)
    in_app_db(app_client, lambda db: _make_admin(db, name))
    return headers


def test_the_calls_need_a_login_and_the_changing_ones_a_csrf_token_and_an_admin(app_client):
    for path in ("/api/admin/manual-quests", "/api/admin/manual-quests/1", "/api/teams/1/manual-quests", "/api/manual-quests/1/standings"):
        assert app_client.get(path).status_code == 401
    mike = sign_in(app_client, "Mike")
    assert app_client.post("/api/admin/manual-quests", json=body()).status_code == 403, "no token"
    assert app_client.post("/api/teams/1/manual-quests/1/enter").status_code == 403, "no token"
    for path in ("/api/admin/manual-quests", "/api/admin/manual-quests/1"):
        assert app_client.get(path).status_code == 403, "not an admin"
    for path in ("", "/1/publish", "/1/cancel", "/1/entries/1/result"):
        assert app_client.post(f"/api/admin/manual-quests{path}", json=body() if not path else {"won": True}, headers=mike).status_code == 403
    assert app_client.patch("/api/admin/manual-quests/1", json={"name": "X"}, headers=mike).status_code == 403


def test_an_admin_writes_publishes_and_judges_an_event_that_a_team_enters(app_client):
    root = an_admin(app_client)
    event = expect(app_client.post("/api/admin/manual-quests", json=body(), headers=root), 201).json()["event"]
    assert (event["status"], event["entered"]) == ("draft", 0)
    expect(app_client.post("/api/admin/manual-quests", json=body(), headers=root), 422)  # (the key is taken)
    changed = expect(app_client.patch(f"/api/admin/manual-quests/{event['id']}", json={"name": "Grand Arena", "max_entries": 4}, headers=root), 200).json()["event"]
    assert (changed["name"], changed["max_entries"], changed["kind"]) == ("Grand Arena", 4, "coliseum")
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    assert expect(app_client.get(f"/api/teams/{team}/manual-quests"), 200).json() == {"events": []}, "a draft is not seen"
    expect(app_client.post(f"/api/teams/{team}/manual-quests/{event['id']}/enter", headers=mike), 404)
    root = log_in(app_client, "Root")
    expect(app_client.post(f"/api/admin/manual-quests/{event['id']}/publish", headers=root), 200)
    mike = log_in(app_client, "Mike")
    seen = expect(app_client.get(f"/api/teams/{team}/manual-quests"), 200).json()["events"]
    assert [(each["key"], "team" in each) for each in seen] == [("arena", False)]
    entered = expect(app_client.post(f"/api/teams/{team}/manual-quests/{event['id']}/enter", headers=mike), 200).json()["event"]
    assert entered["team"]["status"] == "active" and entered["team"]["deadline"] == entered["team"]["started"] + DAY
    assert expect(app_client.post(f"/api/teams/{team}/manual-quests/{event['id']}/enter", headers=mike), 200).json()["event"]["team"]["id"] == entered["team"]["id"]
    root = log_in(app_client, "Root")
    entry = entered["team"]["id"]
    expect(app_client.post(f"/api/admin/manual-quests/{event['id'] + 1}/entries/{entry}/result", json={"won": True}, headers=root), 404)
    judged = expect(app_client.post(f"/api/admin/manual-quests/{event['id']}/entries/{entry}/result", json={"won": True, "score": 77}, headers=root), 200).json()["entry"]
    assert (judged["status"], judged["score"]) == ("completed", 77)
    expect(app_client.post(f"/api/admin/manual-quests/{event['id']}/entries/{entry}/result", json={"won": True}, headers=root), 422)
    detail = expect(app_client.get(f"/api/admin/manual-quests/{event['id']}"), 200).json()
    assert detail["event"]["entered"] == 1 and [(each["team"], each["score"]) for each in detail["entries"]] == [("Vanguard", 77)]
    assert expect(app_client.get("/api/admin/manual-quests?status=published"), 200).json()["events"][0]["entered"] == 1
    assert expect(app_client.get("/api/admin/manual-quests?status=draft"), 200).json() == {"events": []}
    standings = expect(app_client.get(f"/api/manual-quests/{event['id']}/standings"), 200).json()
    assert standings == {"event": "Grand Arena", "standings": [{"rank": 1, "team": "Vanguard", "score": 77, "status": "completed"}]}


def test_an_admin_cancels_an_event_and_a_draft_has_no_standings(app_client):
    root = an_admin(app_client)
    draft = expect(app_client.post("/api/admin/manual-quests", json=body(), headers=root), 201).json()["event"]["id"]
    expect(app_client.get(f"/api/manual-quests/{draft}/standings"), 404)
    expect(app_client.post(f"/api/admin/manual-quests/{draft}/cancel", headers=root), 200)
    expect(app_client.post(f"/api/admin/manual-quests/{draft}/cancel", headers=root), 422)
    expect(app_client.post("/api/admin/manual-quests/999/publish", headers=root), 404)
    expect(app_client.patch(f"/api/admin/manual-quests/{draft}", json={"name": "Again"}, headers=root), 422)


def test_the_models_are_strict(app_client):
    root = an_admin(app_client)
    for bad in ({**body(), "surprise": 1}, {**body(), "limit_seconds": -1}, {**body(), "key": ""}, {k: v for k, v in body().items() if k != "name"}):
        expect(app_client.post("/api/admin/manual-quests", json=bad, headers=root), 422)
    event = expect(app_client.post("/api/admin/manual-quests", json=body(), headers=root), 201).json()["event"]["id"]
    for bad in ({"name": None}, {"nothing": 1}, {"opens_at": body()["ends_at"] + 1}):
        expect(app_client.patch(f"/api/admin/manual-quests/{event}", json=bad, headers=root), 422)
    expect(app_client.get("/api/admin/manual-quests?status=nope"), 422)


def test_nobody_enters_with_another_players_team(app_client):
    root = an_admin(app_client)
    event = expect(app_client.post("/api/admin/manual-quests", json=body(), headers=root), 201).json()["event"]["id"]
    expect(app_client.post(f"/api/admin/manual-quests/{event}/publish", headers=root), 200)
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    zed = sign_in(app_client, "Zed")
    expect(app_client.get(f"/api/teams/{team}/manual-quests"), 404)
    expect(app_client.post(f"/api/teams/{team}/manual-quests/{event}/enter", headers=zed), 404)


def test_writing_and_entering_are_rate_limited(app_client, monkeypatch):
    monkeypatch.setattr(ratelimit, "MANUAL_QUEST_BY_ACCOUNT", ratelimit.Limit("manual-quest", 2, 3600))
    root = an_admin(app_client)
    expect(app_client.post("/api/admin/manual-quests", json=body("a"), headers=root), 201)
    expect(app_client.post("/api/admin/manual-quests", json=body("b"), headers=root), 201)
    expect(app_client.post("/api/admin/manual-quests", json=body("c"), headers=root), 429)


# --- the dialog tag ----------------------------------------------------------------------------------------------------

def test_the_manual_quest_tag_is_checked_when_the_dialog_is_parsed():
    from terraforma.npcs.script import ScriptError, parse
    parse("`manual_quest,arena,enter,end``manual_quest,arena,finish_win,end``manual_quest,arena,finish_lose,end`done")
    for bad, why in (("`manual_quest,arena,enter`", "takes 3 parameters"), ("`manual_quest,arena,start,end`", "the action"),
                     ("`manual_quest,,enter,end`", "an event key"), ("`manual_quest,arena,enter,nowhere`", "nowhere")):
        with pytest.raises(ScriptError, match=why):
            parse(bad)


async def test_a_dialog_enters_and_finishes_an_event(db):
    p = await a_party(db)
    db.info["manual_quests"] = hooks = Hooks()
    aria, bram = p.heroes[0], p.heroes[1]

    def says(action):
        return check(f"manual_quest,arena,{action},no")

    assert await npc_says(db, aria, says("enter")) == "no", "no such event"
    await a_published(db)
    assert await npc_says(db, aria, says("finish_win")) == "no", "not entered"
    assert await npc_says(db, aria, says("enter")) == "yes"
    assert await npc_says(db, aria, says("finish_lose")) == "yes" and hooks.penalties == [(p.teams[0], "arena", "poor_performance")]
    assert await npc_says(db, aria, says("finish_lose")) == "no", "nothing running"
    assert await npc_says(db, bram, says("enter")) == "yes", "another team of the party enters on its own"
    assert await npc_says(db, bram, says("finish_win")) == "yes" and await quests.completed(db, p.teams[1], "arena", 0) == 1


async def test_a_dialog_turned_away_by_the_game_goes_to_the_label(db):
    p = await a_party(db)
    await a_published(db)
    db.info["manual_quests"] = Hooks(refuse_team=p.teams[0])
    assert await npc_says(db, p.heroes[0], check("manual_quest,arena,enter,no")) == "no"
