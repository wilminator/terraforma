"""Weekly quests: one per team per week, from a template or generated, every generated one kept and promotable. On every database."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from terraforma import wallclock
from terraforma.accounts import ratelimit
from terraforma.accounts.service import create_account
from terraforma.challenge import service as challenge
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.content.schema import ContentError
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Hero
from terraforma.models import Account, World
from terraforma.quests import service as quests
from terraforma.quests import weekly
from terraforma.quests.hooks import GeneratedQuest, WeeklyQuests
from terraforma.quests.weekly_models import QuestTemplate, WeeklyQuest
from terraforma.testing import in_app_db, make_team

from .helpers import expect
from .test_towns import sign_in

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}]}


class Maker(WeeklyQuests):
    """A game that makes up a rat hunt scaled to the level."""

    def generate(self, rng, team_id, level):
        return GeneratedQuest(f"Rats {level}", {"kill": {"rat": level * 2}, "roll": rng.randint(1, 1_000_000)})


class Harder(Maker):
    async def difficulty(self, session, team_id):
        return 2


class Mixed(Maker):
    """Never uses a template."""

    def use_template(self, rng, fitting):
        return False


def template(key="cellar", name="Cellar", low=1, high=999, weight=1, spec=None):
    return {"key": key, "name": name, "min_level": low, "max_level": high, "weight": weight, "spec": spec if spec is not None else {"kill": {"rat": 3}}}


async def a_team(db, name="Vanguard", owner_name="Mike", level=1):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    owner = await db.scalar(select(Account).where(Account.username == owner_name)) or await create_account(
        db, owner_name, PASSWORD, email=f"{owner_name.lower()}@example.com", confirmed=True)
    team = await heroes.create_team(db, owner, name)
    hero = await heroes.create_hero(db, owner, f"{name} hero", "fighter")
    await heroes.add_to_team(db, owner, team.id, hero.id)
    hero.level = level
    await db.flush()
    return team


async def with_templates(db, *rows):
    await load_content(db, {**SEED, "quest_templates": list(rows)})


# --- the week ----------------------------------------------------------------------------------------------------------

def test_weeks_begin_on_monday_midnight_utc():
    monday = 4 * 24 * 60 * 60  # 1970-01-05 00:00 UTC, the first Monday
    assert weekly.week_of(monday) == 0 and weekly.week_of(monday - 1) == -1
    assert weekly.week_of(monday + weekly.WEEK_SECONDS - 1) == 0 and weekly.week_of(monday + weekly.WEEK_SECONDS) == 1
    assert weekly.week_ends(0).isoformat() == "1970-01-12T00:00:00+00:00"


# --- the draw ----------------------------------------------------------------------------------------------------------

async def test_a_team_gets_one_quest_a_week_and_asking_again_returns_it(db, later):
    team = await a_team(db)
    first = await weekly.offer(db, Maker(), team.id)
    assert first.source == "generated" and first.status == "offered" and first.team_id == team.id
    assert (await weekly.offer(db, Maker(), team.id)).id == first.id
    assert await db.scalar(select(func.count()).select_from(WeeklyQuest)) == 1
    later(weekly.WEEK_SECONDS)
    second = await weekly.offer(db, Maker(), team.id)
    assert second.id != first.id and second.week == first.week + 1


async def test_the_draw_replays_from_the_worlds_seed(db):
    first, second = await a_team(db, "Vanguard"), await a_team(db, "Rearguard")
    await db.execute(update(World).values(seed=1234))
    one, two = await weekly.offer(db, Maker(), first.id), await weekly.offer(db, Maker(), second.id)
    assert one.spec["roll"] != two.spec["roll"], "each team has its own stream"
    await db.delete(one)
    await db.flush()
    again = await weekly.offer(db, Maker(), first.id)
    assert again.spec == one.spec, "the same seed, team and week draw the same quest"


async def test_the_quest_level_is_the_teams_plus_its_difficulty(db):
    team = await a_team(db, level=5)
    assert (await weekly.offer(db, Maker(), team.id)).level == 5
    other = await a_team(db, "Rearguard", level=5)
    assert (await weekly.offer(db, Harder(), other.id)).level == 7


async def test_a_template_that_fits_the_level_is_scaled_and_one_that_does_not_is_left_out(db):
    await with_templates(db, template("low", "Low", 1, 4), template("mid", "Mid", 5, 9, spec={"kill": {"rat": 10}}))
    team = await a_team(db, level=6)

    class Scaled(Maker):
        def scale(self, tpl, level):
            return {**tpl.spec, "level": level}

    quest = await weekly.offer(db, Scaled(), team.id)
    assert (quest.source, quest.name, quest.spec) == ("template", "Mid", {"kill": {"rat": 10}, "level": 6})
    assert quest.template_id == (await db.scalar(select(QuestTemplate.id).where(QuestTemplate.key == "mid")))


async def test_templates_are_drawn_by_weight(db):
    await with_templates(db, template("common", "Common", weight=20), template("rare", "Rare", weight=1))
    await a_team(db, "First")  # (makes the world)
    await db.execute(update(World).values(seed=7))  # (the draw is fixed, so the test does not depend on luck)
    drawn = [(await weekly.offer(db, Maker(), (await a_team(db, f"Team {number}", f"Owner{number}")).id)).name for number in range(30)]
    assert drawn.count("Common") > drawn.count("Rare") >= 0 and set(drawn) <= {"Common", "Rare"}
    assert drawn.count("Common") >= 20, "at 20 to 1 nearly every draw is the common one"


async def test_a_game_can_ask_for_generated_quests_even_when_templates_fit(db):
    await with_templates(db, template())
    team = await a_team(db)
    assert (await weekly.offer(db, Mixed(), team.id)).source == "generated"


async def test_a_team_nothing_fits_has_no_quest_when_the_game_makes_none(db):
    await with_templates(db, template(low=50))
    team = await a_team(db)
    assert await weekly.offer(db, WeeklyQuests(), team.id) is None
    assert await db.scalar(select(func.count()).select_from(WeeklyQuest)) == 0


async def test_a_quest_for_a_team_that_does_not_exist_is_refused(db):
    with pytest.raises(weekly.WeeklyError):
        await weekly.offer(db, Maker(), 999)


async def test_the_default_level_is_the_average_of_the_teams_heroes(db):
    team = await a_team(db, level=4)
    assert await WeeklyQuests().level(db, team.id) == 4
    assert await WeeklyQuests().level(db, 999) == 1


async def test_a_spec_that_is_not_json_or_too_big_is_refused(db):
    team = await a_team(db)

    class Huge(WeeklyQuests):
        def generate(self, rng, team_id, level):
            return GeneratedQuest("Huge", {"text": "x" * 20_000})

    with pytest.raises(weekly.WeeklyError):
        await weekly.offer(db, Huge(), team.id)


# --- playing it --------------------------------------------------------------------------------------------------------

async def test_a_started_quest_won_is_added_to_the_teams_record(db):
    team = await a_team(db, level=3)
    quest = await weekly.offer(db, Maker(), team.id)
    with pytest.raises(weekly.WeeklyError):
        await weekly.finish(db, Maker(), quest.id, True)  # (not started)
    await weekly.start(db, Maker(), quest.id)
    with pytest.raises(weekly.WeeklyError):
        await weekly.start(db, Maker(), quest.id)
    won = await weekly.finish(db, Maker(), quest.id, True)
    assert won.status == "completed" and won.finished_at is not None
    assert await quests.completed(db, team.id, weekly.CATEGORY, 3) == 1
    with pytest.raises(weekly.WeeklyError):
        await weekly.finish(db, Maker(), quest.id, False)


async def test_a_failed_quest_is_lost_for_the_week_and_records_nothing(db):
    team = await a_team(db)
    quest = await weekly.offer(db, Maker(), team.id)
    await weekly.start(db, Maker(), quest.id)
    assert (await weekly.finish(db, Maker(), quest.id, False)).status == "failed"
    assert (await weekly.offer(db, Maker(), team.id)).id == quest.id, "the same, failed quest: no second try"
    assert await quests.completed(db, team.id, weekly.CATEGORY) == 0


class Patient(Maker):
    """A game that gives a taken quest two weeks."""

    def limit_seconds(self, level):
        return 2 * weekly.WEEK_SECONDS


class Penalizing(Maker):
    """A game that records what failing costs."""

    def __init__(self):
        self.failed = []

    async def penalty(self, session, team_id, quest, reason):
        self.failed.append((team_id, quest.id, reason))


async def test_a_quest_not_taken_in_its_week_lapses_but_one_taken_may_finish_late(db, later):
    team = await a_team(db)
    lapsing, taken = await weekly.offer(db, Patient(), team.id), None
    await weekly.start(db, Patient(), lapsing.id)
    other = await a_team(db, "Rearguard")
    taken = await weekly.offer(db, Patient(), other.id)
    later(weekly.WEEK_SECONDS)
    with pytest.raises(weekly.WeeklyError):
        await weekly.start(db, Patient(), taken.id)
    assert (await weekly.finish(db, Patient(), lapsing.id, True)).status == "completed"


# --- the time limit and the penalty ------------------------------------------------------------------------------------

async def test_a_taken_quest_has_a_deadline_of_the_games_limit_a_day_by_default(db):
    quest = await weekly.offer(db, Maker(), (await a_team(db)).id)
    started = await weekly.start(db, Maker(), quest.id)
    assert started.deadline == started.started_at + 24 * 60 * 60
    assert weekly.view(started)["deadline"] == started.deadline
    other = await weekly.offer(db, Patient(), (await a_team(db, "Rearguard")).id)
    taken = await weekly.start(db, Patient(), other.id)
    assert taken.deadline == taken.started_at + 2 * weekly.WEEK_SECONDS


async def test_a_quest_not_finished_by_its_deadline_fails_with_timeout_and_costs_the_penalty(db, later):
    hooks, team = Penalizing(), await a_team(db)
    quest = await weekly.offer(db, hooks, team.id)
    await weekly.start(db, hooks, quest.id)
    later(24 * 60 * 60)
    assert (await weekly.active(db, hooks, team.id)) is not None, "right at the deadline there is still time"
    later(24 * 60 * 60 + 1)
    assert await weekly.active(db, hooks, team.id) is None
    failed = await weekly.get(db, quest.id)
    assert failed.status == "failed" and failed.failure == "timeout" and failed.finished_at is not None
    assert hooks.failed == [(team.id, quest.id, "timeout")]
    assert await weekly.active(db, hooks, team.id) is None and len(hooks.failed) == 1, "the penalty is charged once"
    assert await quests.completed(db, team.id, weekly.CATEGORY) == 0


async def test_a_win_reported_after_the_deadline_is_a_timeout_not_a_win(db, later):
    hooks, team = Penalizing(), await a_team(db)
    quest = await weekly.offer(db, hooks, team.id)
    await weekly.start(db, hooks, quest.id)
    later(24 * 60 * 60 + 1)
    late = await weekly.finish(db, hooks, quest.id, True)
    assert late.status == "failed" and late.failure == "timeout"
    assert await quests.completed(db, team.id, weekly.CATEGORY) == 0
    assert hooks.failed == [(team.id, quest.id, "timeout")]


async def test_a_loss_in_time_is_poor_performance_unless_the_game_says_otherwise(db):
    hooks, team = Penalizing(), await a_team(db)
    quest = await weekly.offer(db, hooks, team.id)
    await weekly.start(db, hooks, quest.id)
    lost = await weekly.finish(db, hooks, quest.id, False)
    assert lost.failure == "poor_performance" and hooks.failed == [(team.id, quest.id, "poor_performance")]
    other, rival = Penalizing(), await a_team(db, "Rearguard")
    second = await weekly.offer(db, other, rival.id)
    await weekly.start(db, other, second.id)
    with pytest.raises(weekly.WeeklyError):
        await weekly.finish(db, other, second.id, False, "bored")
    assert (await weekly.finish(db, other, second.id, False, "timeout")).failure == "timeout"


async def test_a_win_in_time_costs_nothing_and_the_default_penalty_does_nothing(db):
    hooks, team = Penalizing(), await a_team(db)
    quest = await weekly.offer(db, hooks, team.id)
    await weekly.start(db, hooks, quest.id)
    assert (await weekly.finish(db, hooks, quest.id, True)).failure is None and hooks.failed == []
    rival = await a_team(db, "Rearguard")
    second = await weekly.offer(db, Maker(), rival.id)
    await weekly.start(db, Maker(), second.id)
    assert (await weekly.finish(db, Maker(), second.id, False)).status == "failed"


async def test_offering_settles_an_overdue_quest_and_a_team_takes_one_quest_at_a_time(db, later):
    hooks, team = Penalizing(), await a_team(db)
    quest = await weekly.offer(db, hooks, team.id)
    await weekly.start(db, hooks, quest.id)
    later(24 * 60 * 60 + 1)
    assert (await weekly.offer(db, hooks, team.id)).status == "failed" and len(hooks.failed) == 1
    later(weekly.WEEK_SECONDS)
    fresh = await weekly.offer(db, Patient(), team.id)
    assert fresh.id != quest.id
    await weekly.start(db, Patient(), fresh.id)
    later(weekly.WEEK_SECONDS * 2)
    again = await weekly.offer(db, Patient(), team.id)
    assert again.id != fresh.id and again.status == "offered", "the last quest was still in time, so the new week's is offered beside it"
    with pytest.raises(weekly.WeeklyError, match="already on a weekly quest"):
        await weekly.start(db, Patient(), again.id)


# --- recording and promoting -------------------------------------------------------------------------------------------

async def test_every_generated_quest_is_recorded_and_only_those_are_listed(db):
    await with_templates(db, template(low=9))
    generated = [await weekly.offer(db, Maker(), (await a_team(db, name, level=1)).id) for name in ("One", "Two")]
    templated = await weekly.offer(db, Maker(), (await a_team(db, "Three", level=9)).id)
    assert templated.source == "template"
    assert [quest.id for quest in await weekly.generated(db)] == [quest.id for quest in generated]
    assert [quest.id for quest in await weekly.generated(db, after=generated[0].id)] == [generated[1].id]
    assert [quest.id for quest in await weekly.generated(db, promoted=True)] == []


async def test_a_deleted_team_takes_its_templated_quests_but_leaves_generated_ones_to_promote(db):
    await with_templates(db, template(low=9))
    gen = await weekly.offer(db, Maker(), (await a_team(db, "One")).id)
    tpl_team = await a_team(db, "Two", level=9)
    await weekly.offer(db, Maker(), tpl_team.id)
    await weekly.forget_team(db, gen.team_id)
    await weekly.forget_team(db, tpl_team.id)
    assert (await db.get(WeeklyQuest, gen.id)).team_id is None
    assert await db.scalar(select(func.count()).select_from(WeeklyQuest)) == 1
    assert (await weekly.promote(db, gen.id, "rats")).spec == gen.spec


async def test_promoting_without_tweaks_keeps_the_quest_and_fits_that_level(db):
    quest = await weekly.offer(db, Maker(), (await a_team(db, level=4)).id)
    promoted = await weekly.promote(db, quest.id, "rat-hunt")
    assert (promoted.name, promoted.spec, promoted.min_level, promoted.max_level, promoted.source) == (quest.name, quest.spec, 4, 4, "promoted")
    assert (await db.get(WeeklyQuest, quest.id)).promoted_template_id == promoted.id
    assert [each.id for each in await weekly.generated(db, promoted=True)] == [quest.id]
    # ... and the next team at that level can be dealt it.
    other = await weekly.offer(db, Maker(), (await a_team(db, "Rearguard", level=4)).id)
    assert (other.source, other.template_id, other.name) == ("template", promoted.id, quest.name)


async def test_promoting_with_tweaks_changes_what_the_template_gets(db):
    quest = await weekly.offer(db, Maker(), (await a_team(db, level=4)).id)
    promoted = await weekly.promote(db, quest.id, "rats", name="Big rats", min_level=2, max_level=8, weight=3, spec={"kill": {"rat": 99}})
    assert (promoted.name, promoted.min_level, promoted.max_level, promoted.weight, promoted.spec) == ("Big rats", 2, 8, 3, {"kill": {"rat": 99}})


async def test_a_quest_can_be_promoted_once_and_only_if_generated_and_the_key_is_fresh(db):
    await with_templates(db, template(low=9))
    quest = await weekly.offer(db, Maker(), (await a_team(db, "One")).id)
    templated = await weekly.offer(db, Maker(), (await a_team(db, "Two", level=9)).id)
    for args in ((templated.id, "x"), (quest.id, "Bad Key"), (quest.id, "ok", None, 5, 2), (quest.id, "cellar"), (999, "ok")):
        with pytest.raises(weekly.WeeklyError):
            await weekly.promote(db, *args)
    await weekly.promote(db, quest.id, "rats")
    with pytest.raises(weekly.WeeklyError):
        await weekly.promote(db, quest.id, "rats-again")


# --- the seed ----------------------------------------------------------------------------------------------------------

async def test_the_seed_adds_updates_and_retires_templates_but_leaves_promoted_ones(db):
    assert (await load_content(db, {**SEED, "quest_templates": [template("a"), template("b")]}))["quest_templates"] == 2
    await load_content(db, {**SEED, "quest_templates": [template("a", "Renamed", 2, 3, 4, {"x": 1})]})
    rows = {row.key: row for row in (await db.scalars(select(QuestTemplate))).all()}
    assert (rows["a"].name, rows["a"].min_level, rows["a"].max_level, rows["a"].weight, rows["a"].spec, rows["a"].active) == ("Renamed", 2, 3, 4, {"x": 1}, True)
    assert rows["b"].active is False, "kept, not deleted"
    quest = await weekly.offer(db, Maker(), (await a_team(db)).id)
    await weekly.promote(db, quest.id, "made")
    await load_content(db, {**SEED, "quest_templates": [template("a")]})
    assert (await db.scalar(select(QuestTemplate.active).where(QuestTemplate.key == "made"))) is True
    with pytest.raises(weekly.WeeklyError):
        await load_content(db, {**SEED, "quest_templates": [template("made")]})


@pytest.mark.parametrize("bad", [
    {"key": "Bad Key", "name": "x"}, {"key": "k", "name": ""}, {"key": "k", "name": "x", "min_level": 5, "max_level": 2},
    {"key": "k", "name": "x", "weight": 0}, {"key": "k", "name": "x", "spec": {"t": "x" * 20_000}}, {"key": "k", "name": "x", "other": 1},
])
async def test_a_bad_template_stops_the_seed(db, bad):
    with pytest.raises(ContentError):
        await load_content(db, {**SEED, "quest_templates": [bad]})


# --- the calls ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    return Game(name="Test Game", seed_dir=seed_dir, weekly_quests=Maker())


def test_the_calls_need_a_login_and_the_changing_ones_a_csrf_token(app_client):
    for method, url in (("GET", "/api/teams/1/weekly-quest"), ("GET", "/api/admin/weekly-quests")):
        assert app_client.request(method, url).status_code == 401, url
    sign_in(app_client, "Mike")
    assert app_client.post("/api/teams/1/weekly-quest/offer").status_code == 403
    assert app_client.post("/api/admin/weekly-quests/1/promote", json={"key": "k"}).status_code == 403


def test_a_player_asks_for_a_quest_and_gets_the_same_one_all_week(app_client, later):
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    assert expect(app_client.get(f"/api/teams/{team}/weekly-quest"), 200).json() == {"quest": None}
    quest = expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=mike), 200).json()["quest"]
    assert set(quest) == {"id", "week", "ends", "name", "level", "status", "spec", "started", "deadline", "failure"} and quest["status"] == "offered"
    assert expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=mike), 200).json()["quest"] == quest
    assert expect(app_client.get(f"/api/teams/{team}/weekly-quest"), 200).json()["quest"] == quest
    later(timedelta(days=8).total_seconds())
    assert expect(app_client.get(f"/api/teams/{team}/weekly-quest"), 200).json() == {"quest": None}


def test_nobody_asks_for_another_players_teams_quest(app_client):
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    zed = sign_in(app_client, "Zed")
    expect(app_client.get(f"/api/teams/{team}/weekly-quest"), 404)
    expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=zed), 404)


def test_asking_for_a_quest_is_rate_limited(app_client, monkeypatch):
    monkeypatch.setattr(ratelimit, "WEEKLY_QUEST_BY_ACCOUNT", ratelimit.Limit("weekly-quest", 2, 3600))
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    for _ in range(2):
        expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=mike), 200)
    expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=mike), 429)


def test_an_admin_lists_the_generated_quests_and_promotes_one_and_nobody_else_may(app_client):
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    expect(app_client.post(f"/api/teams/{team}/weekly-quest/offer", headers=mike), 200)
    expect(app_client.get("/api/admin/weekly-quests"), 403)
    expect(app_client.post("/api/admin/weekly-quests/1/promote", json={"key": "rats"}, headers=mike), 403)
    root = sign_in(app_client, "Root")
    in_app_db(app_client, lambda db: _make_admin(db, "Root"))
    listing = expect(app_client.get("/api/admin/weekly-quests"), 200).json()["quests"]
    assert len(listing) == 1 and listing[0]["team_id"] == team and listing[0]["source"] == "generated" and listing[0]["promoted_template_id"] is None
    quest_id = listing[0]["id"]
    expect(app_client.post(f"/api/admin/weekly-quests/{quest_id}/promote", json={"key": "Bad Key"}, headers=root), 422)
    expect(app_client.post(f"/api/admin/weekly-quests/{quest_id}/promote", json={"key": "rats", "extra": 1}, headers=root), 422)
    template_ = expect(app_client.post(f"/api/admin/weekly-quests/{quest_id}/promote", json={"key": "rats", "name": "Big rats", "weight": 2}, headers=root), 200).json()["template"]
    assert (template_["key"], template_["name"], template_["weight"]) == ("rats", "Big rats", 2)
    expect(app_client.post(f"/api/admin/weekly-quests/{quest_id}/promote", json={"key": "again"}, headers=root), 422)
    expect(app_client.post("/api/admin/weekly-quests/999/promote", json={"key": "new"}, headers=root), 404)
    assert expect(app_client.get("/api/admin/weekly-quests?promoted=false"), 200).json()["quests"] == []
    assert len(expect(app_client.get("/api/admin/weekly-quests?promoted=true"), 200).json()["quests"]) == 1


async def _make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


# --- the dialog tag ----------------------------------------------------------------------------------------------------

def test_the_weekly_quest_tag_is_checked_when_the_dialog_is_parsed():
    from terraforma.npcs.script import ScriptError, parse
    parse("`weekly_quest,offer,end``weekly_quest,start,end``weekly_quest,finish_win,end``weekly_quest,finish_lose,end`done")
    for bad, why in (("`weekly_quest,offer`", "takes 2 parameters"), ("`weekly_quest,take,end`", "the action"), ("`weekly_quest,offer,nowhere`", "nowhere")):
        with pytest.raises(ScriptError, match=why):
            parse(bad)


async def test_a_dialog_offers_starts_and_finishes_the_weekly_quest(db):
    from .test_npc_state import a_party, check, npc_says

    p = await a_party(db)
    db.info["weekly_quests"] = Maker()
    aria, bram = p.heroes[0], p.heroes[1]

    def says(tag):
        return check(f"weekly_quest,{tag},no")

    assert await npc_says(db, aria, says("start")) == "no", "nothing offered yet"
    assert await npc_says(db, aria, says("finish_win")) == "no", "nothing active"
    assert await npc_says(db, aria, says("offer")) == "yes"
    quest = await weekly.current(db, p.teams[0])
    assert quest.status == "offered" and await weekly.current(db, p.teams[1]) is None, "Aria's own team, not the party's"
    assert await npc_says(db, aria, says("finish_win")) == "no", "offered, not active"
    assert await npc_says(db, aria, says("start")) == "yes" and quest.status == "active"
    assert await npc_says(db, aria, says("start")) == "no", "already started"
    assert await npc_says(db, aria, says("finish_lose")) == "yes" and quest.status == "failed"
    assert await npc_says(db, aria, says("offer")) == "no", "finished this week"
    assert await npc_says(db, bram, says("offer")) == "yes", "another team of the party has its own"
    assert await quests.completed(db, p.teams[0], weekly.CATEGORY) == 0


async def test_a_dialog_finishing_a_win_records_it_even_after_the_week_turns(db, later):
    from .test_npc_state import a_party, check, npc_says

    p = await a_party(db)
    db.info["weekly_quests"] = Patient()
    aria = p.heroes[0]
    assert await npc_says(db, aria, "`weekly_quest,offer,no``weekly_quest,start,no`yes`jump,end``label,no`no") == "yes"
    later(weekly.WEEK_SECONDS)
    assert await npc_says(db, aria, "`weekly_quest,finish_win,no`yes`jump,end``label,no`no") == "yes"
    assert await quests.completed(db, p.teams[0], weekly.CATEGORY) == 1


async def test_a_dialog_finishing_after_the_deadline_finds_the_quest_already_failed(db, later):
    from .test_npc_state import a_party, npc_says

    p = await a_party(db)
    db.info["weekly_quests"] = hooks = Penalizing()
    aria = p.heroes[0]
    assert await npc_says(db, aria, "`weekly_quest,offer,no``weekly_quest,start,no`yes`jump,end``label,no`no") == "yes"
    later(24 * 60 * 60 + 1)
    assert await npc_says(db, aria, "`weekly_quest,finish_win,no`yes`jump,end``label,no`no") == "no", "no quest in time to finish"
    assert await quests.completed(db, p.teams[0], weekly.CATEGORY) == 0
    assert [reason for _, _, reason in hooks.failed] == ["timeout"]


async def test_a_game_that_makes_no_quest_sends_the_dialog_to_the_label(db):
    from .test_npc_state import a_party, check, npc_says

    p = await a_party(db)  # (no hooks on the session: the engine's, which make none)
    assert await npc_says(db, p.heroes[0], "`weekly_quest,offer,no`yes`jump,end``label,no`no") == "no"
