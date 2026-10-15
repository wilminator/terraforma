"""Rotating quests: a slot holds one quest at a time and changes it after a random while; teams take it, race its clock and may be penalized.
On every database."""

import json

import pytest
from sqlalchemy import func, select, update

from terraforma import wallclock
from terraforma.accounts import ratelimit
from terraforma.challenge import service as challenge
from terraforma.content.loader import load_content
from terraforma.content.schema import ContentError
from terraforma.game import Game
from terraforma.models import Account, World
from terraforma.quests import rotation
from terraforma.quests import service as quests
from terraforma.quests import weekly
from terraforma.quests.hooks import GeneratedQuest, RotatingQuests, WeeklyQuests
from terraforma.quests.rotation_models import QuestSlot, SlotQuest, TeamSlotQuest
from terraforma.testing import in_app_db, make_team
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_npc_state import a_party, check, npc_says
from .test_towns import sign_in
from .test_weekly_quests import SEED, a_team

pytestmark = pytest.mark.anyio

DAY = rotation.DAY
#: A period lasts 10 to 20 days; a team has 5 days to finish once it takes the quest, and 3 more days after the quest rotated out.
SLOT = {"key": "rank-s", "name": "Rank S promotion", "guild": "vanguard", "rank": 5, "min_days": 10, "max_days": 20, "grace_days": 3, "limit_days": 5}


def template(key, name="Dragon", slot="rank-s", weight=1, spec=None):
    return {"key": key, "name": name, "slot": slot, "weight": weight, "spec": spec if spec is not None else {"boss": key}}


class Hooks(RotatingQuests):
    """A game that makes up a quest when no template fits and keeps the penalties it was asked to charge."""

    def __init__(self):
        self.penalties = []

    def generate(self, rng, slot, seq):
        return GeneratedQuest(f"Generated {seq}", {"roll": rng.randint(1, 1_000_000), "seq": seq})

    async def penalty(self, session, team_id, slot, reason):
        self.penalties.append((team_id, slot.key, reason))


async def a_world(db, *templates, slots=None):
    """The slots and templates loaded, and the world's seed fixed (a period's length and quest are drawn from it)."""
    await load_content(db, {**SEED, "quest_slots": [SLOT] if slots is None else slots, "quest_templates": list(templates)})
    await ensure_start(db)
    await db.execute(update(World).values(seed=4242))
    return await rotation.get_slot(db, "rank-s")


# --- the seed ----------------------------------------------------------------------------------------------------------

async def test_the_seed_adds_updates_and_retires_slots_and_keeps_the_first_periods_start(db, later):
    start = wallclock.timestamp()
    assert (await load_content(db, {**SEED, "quest_slots": [SLOT]}))["quest_slots"] == 1
    first = await rotation.get_slot(db, "rank-s")
    assert (first.min_seconds, first.max_seconds, first.grace_seconds, first.limit_seconds, first.anchor) == (10 * DAY, 20 * DAY, 3 * DAY, 5 * DAY, start)
    later(DAY)
    await load_content(db, {**SEED, "quest_slots": [{**SLOT, "name": "Renamed", "min_days": 30, "max_days": 40}]})
    again = await rotation.get_slot(db, "rank-s")
    assert (again.name, again.min_seconds, again.anchor) == ("Renamed", 30 * DAY, start), "changed, but the slot did not start over"
    await load_content(db, {**SEED, "quest_slots": []})
    with pytest.raises(rotation.RotationError):
        await rotation.get_slot(db, "rank-s")
    assert await db.scalar(select(func.count()).select_from(QuestSlot)) == 1, "kept, not deleted"
    await load_content(db, {**SEED, "quest_slots": [SLOT]})
    assert (await rotation.get_slot(db, "rank-s")).anchor == start


@pytest.mark.parametrize("bad", [
    {**SLOT, "key": "Bad Key"}, {**SLOT, "name": ""}, {**SLOT, "min_days": 21}, {**SLOT, "limit_days": 0}, {**SLOT, "min_days": 0},
    {**SLOT, "grace_days": -1}, {**SLOT, "rank": 100}, {**SLOT, "other": 1},
])
async def test_a_bad_slot_stops_the_seed(db, bad):
    with pytest.raises(ContentError):
        await load_content(db, {**SEED, "quest_slots": [bad]})


async def test_a_template_for_a_slot_the_seed_does_not_have_stops_the_seed(db):
    with pytest.raises(ContentError, match="quest_slots.json doesn't have|which quest_slots.json"):
        await load_content(db, {**SEED, "quest_slots": [SLOT], "quest_templates": [template("a", slot="nowhere")]})


# --- the schedule ------------------------------------------------------------------------------------------------------

async def test_a_period_lasts_a_random_time_in_the_slots_range_and_replays_from_the_seed(db):
    slot = await a_world(db, template("a"))
    quest = await rotation.current(db, Hooks(), slot)
    assert quest.starts_at == slot.anchor and 10 * DAY <= quest.ends_at - quest.starts_at <= 20 * DAY
    await db.delete(quest)
    await db.flush()
    again = await rotation.current(db, Hooks(), slot)
    assert (again.starts_at, again.ends_at, again.spec) == (quest.starts_at, quest.ends_at, quest.spec), "same seed, slot and period"


async def test_the_quest_holds_until_the_period_ends_and_then_changes_to_another_template(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a", "Dragon"), template("b", "Lich"))
    first = await rotation.current(db, Hooks(), slot)
    assert (await rotation.current(db, Hooks(), slot)).id == first.id
    later(first.ends_at - start - 1)
    assert (await rotation.current(db, Hooks(), slot)).id == first.id, "still the same, one second before"
    later(first.ends_at - start)
    second = await rotation.current(db, Hooks(), slot)
    assert (second.seq, second.starts_at) == (1, first.ends_at) and second.template_id != first.template_id and second.name != first.name


async def test_a_slot_with_one_template_repeats_it(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"))
    first = await rotation.current(db, Hooks(), slot)
    later(first.ends_at - start)
    second = await rotation.current(db, Hooks(), slot)
    assert second.id != first.id and second.template_id == first.template_id


async def test_periods_nobody_asked_about_are_walked_over_and_leave_no_rows(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"))
    await rotation.current(db, Hooks(), slot)
    later(2 * 365 * DAY)
    quest = await rotation.current(db, Hooks(), slot)
    assert quest.seq >= 36 and quest.starts_at <= start + 2 * 365 * DAY < quest.ends_at
    assert await db.scalar(select(func.count()).select_from(SlotQuest)) == 2


async def test_a_slot_whose_first_period_has_not_begun_holds_nothing(db, later):
    slot = await a_world(db, template("a"))
    await db.execute(update(QuestSlot).values(anchor=wallclock.timestamp() + DAY))
    await db.refresh(slot)
    assert await rotation.current(db, Hooks(), slot) is None


async def test_slot_templates_are_never_dealt_as_weekly_quests(db):
    await a_world(db, template("a"))
    team = await a_team(db)
    assert await weekly.offer(db, WeeklyQuests(), team.id) is None


async def test_the_game_makes_a_quest_when_no_template_is_active_and_a_quest_of_nothing_offers_none(db):
    slot = await a_world(db)
    made = await rotation.current(db, Hooks(), slot)
    assert (made.source, made.name, made.spec["seq"]) == ("generated", "Generated 0", 0)
    await db.delete(made)
    await db.flush()
    none = await rotation.current(db, RotatingQuests(), slot)
    assert (none.source, none.name, none.spec) == ("nothing", "", {})
    team = await a_team(db)
    assert await rotation.offer(db, RotatingQuests(), team.id, "rank-s") is None


# --- taking and finishing a quest ----------------------------------------------------------------------------------------

async def test_a_team_takes_a_quest_wins_it_and_it_is_recorded_at_the_slots_rank(db):
    await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    offered = await rotation.offer(db, hooks, team.id, "rank-s")
    assert offered.status == "offered" and (await rotation.offer(db, hooks, team.id, "rank-s")).id == offered.id
    with pytest.raises(rotation.RotationError):
        await rotation.finish(db, hooks, offered.id, True)  # (not taken)
    now = wallclock.timestamp()
    taken = await rotation.start(db, hooks, offered.id)
    assert (taken.status, taken.started_at, taken.deadline) == ("active", now, now + 5 * DAY), "the time limit, since the quest holds longer"
    with pytest.raises(rotation.RotationError):
        await rotation.start(db, hooks, offered.id)
    done = await rotation.finish(db, hooks, offered.id, True)
    assert done.status == "completed" and done.finished_at == now
    assert await quests.completed(db, team.id, "rank-s", 5) == 1 and hooks.penalties == []
    assert (await rotation.offer(db, hooks, team.id, "rank-s")).status == "completed", "no second try in the same period"
    with pytest.raises(rotation.RotationError):
        await rotation.finish(db, hooks, offered.id, False)


async def test_a_quest_lost_for_poor_performance_costs_what_the_game_says(db):
    await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    await rotation.start(db, hooks, progress.id)
    lost = await rotation.finish(db, hooks, progress.id, False)
    assert (lost.status, lost.failure) == ("failed", "poor_performance")
    assert hooks.penalties == [(team.id, "rank-s", "poor_performance")] and await quests.completed(db, team.id, "rank-s") == 0


async def test_a_game_cannot_fail_a_quest_for_a_reason_the_engine_does_not_know(db):
    await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    await rotation.start(db, hooks, progress.id)
    with pytest.raises(rotation.RotationError):
        await rotation.finish(db, hooks, progress.id, False, "world")
    assert hooks.penalties == []


async def test_a_team_that_runs_out_of_time_fails_even_when_it_would_have_won(db, later):
    start = wallclock.timestamp()
    await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    await rotation.start(db, hooks, progress.id)
    later(5 * DAY)
    assert (await rotation.finish(db, hooks, progress.id, True)).status == "completed", "on the last second"
    other = await a_team(db, "Rearguard")
    late = await rotation.offer(db, hooks, other.id, "rank-s")
    later(6 * DAY - 1)
    await rotation.start(db, hooks, late.id)
    later(6 * DAY + 5 * DAY)
    result = await rotation.finish(db, hooks, late.id, True)
    assert (result.status, result.failure) == ("failed", "timeout")
    assert hooks.penalties == [(other.id, "rank-s", "timeout")] and await quests.completed(db, other.id, "rank-s") == 0
    assert start < wallclock.timestamp()


async def test_an_overdue_quest_is_settled_the_next_time_anything_touches_the_team_and_only_once(db, later):
    await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    await rotation.start(db, hooks, progress.id)
    later(5 * DAY + 1)
    assert (await rotation.active(db, team.id, "rank-s")).id == progress.id, "nothing has touched the team yet"
    await rotation.settle(db, hooks, team.id)
    await rotation.settle(db, hooks, team.id)
    assert await rotation.active(db, team.id, "rank-s") is None
    assert (await rotation.get(db, progress.id)).failure == "timeout" and len(hooks.penalties) == 1


async def test_a_team_that_took_the_quest_keeps_it_through_the_grace_time_after_it_rotates_out(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    quest = await rotation.current(db, hooks, slot)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    later(quest.ends_at - start - DAY)
    taken = await rotation.start(db, hooks, progress.id)
    assert taken.deadline == quest.ends_at + 3 * DAY, "the limit would be 4 days past the rotation: the grace time cuts it to 3"
    later(quest.ends_at - start + 2 * DAY)
    assert (await rotation.current(db, hooks, slot)).id != quest.id, "the slot has moved on"
    assert (await rotation.finish(db, hooks, progress.id, True)).status == "completed"


async def test_a_team_that_does_not_finish_within_the_grace_time_fails(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"))
    hooks, team = Hooks(), await a_team(db)
    quest = await rotation.current(db, hooks, slot)
    progress = await rotation.offer(db, hooks, team.id, "rank-s")
    later(quest.ends_at - start - DAY)
    await rotation.start(db, hooks, progress.id)
    later(quest.ends_at - start + 3 * DAY + 1)
    assert (await rotation.finish(db, hooks, progress.id, True)).failure == "timeout"


async def test_an_offer_nobody_took_lapses_at_the_rotation_with_no_penalty_and_the_new_quest_can_be_offered(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"), template("b"))
    hooks, team = Hooks(), await a_team(db)
    quest = await rotation.current(db, hooks, slot)
    old = await rotation.offer(db, hooks, team.id, "rank-s")
    later(quest.ends_at - start)
    new = await rotation.offer(db, hooks, team.id, "rank-s")
    assert (await rotation.get(db, old.id)).status == "lapsed" and hooks.penalties == []
    assert new.id != old.id and new.status == "offered" and new.slot_quest_id != old.slot_quest_id
    with pytest.raises(rotation.RotationError):
        await rotation.start(db, hooks, old.id)


async def test_a_team_takes_one_quest_of_a_slot_at_a_time(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"), template("b"))
    hooks, team = Hooks(), await a_team(db)
    quest = await rotation.current(db, hooks, slot)
    first = await rotation.offer(db, hooks, team.id, "rank-s")
    later(quest.ends_at - start - DAY)
    await rotation.start(db, hooks, first.id)
    later(quest.ends_at - start + DAY)
    second = await rotation.offer(db, hooks, team.id, "rank-s")
    assert second.id != first.id
    with pytest.raises(rotation.RotationError, match="already on a quest"):
        await rotation.start(db, hooks, second.id)
    await rotation.finish(db, hooks, first.id, True)
    assert (await rotation.start(db, hooks, second.id)).status == "active"


async def test_asking_about_a_missing_team_slot_or_quest_is_refused(db):
    await a_world(db, template("a"))
    team = await a_team(db)
    for args in ((999, "rank-s"), (team.id, "nowhere")):
        with pytest.raises(rotation.RotationError):
            await rotation.offer(db, Hooks(), *args)
    with pytest.raises(rotation.RotationError):
        await rotation.start(db, Hooks(), 999)


# --- the world ends a quest ----------------------------------------------------------------------------------------------

async def test_the_world_ending_a_quest_releases_every_team_with_no_penalty_and_the_slot_draws_again(db, later):
    start = wallclock.timestamp()
    slot = await a_world(db, template("a"), template("b"))
    hooks = Hooks()
    holding, offered, bystander = await a_team(db, "Holding"), await a_team(db, "Offered"), await a_team(db, "Bystander")
    quest = await rotation.current(db, hooks, slot)
    taken = await rotation.offer(db, hooks, holding.id, "rank-s")
    await rotation.start(db, hooks, taken.id)
    waiting = await rotation.offer(db, hooks, offered.id, "rank-s")
    later(3 * DAY)
    ended = await rotation.end_early(db, hooks, "rank-s")
    assert ended.id == quest.id and ended.ended_early and ended.ends_at == start + 3 * DAY
    for progress in (taken, waiting):
        await db.refresh(progress)
        assert (progress.status, progress.failure, progress.finished_at) == ("ended", "world", start + 3 * DAY)
    assert hooks.penalties == [] and await rotation.progress_of(db, bystander.id, quest) is None
    with pytest.raises(rotation.RotationError):
        await rotation.finish(db, hooks, taken.id, True)
    new = await rotation.current(db, hooks, slot)
    assert (new.seq, new.starts_at) == (quest.seq + 1, start + 3 * DAY) and new.id != quest.id
    assert (await rotation.offer(db, hooks, holding.id, "rank-s")).slot_quest_id == new.id, "the team is free to take the new quest"


async def test_ending_a_slot_that_holds_no_quest_does_nothing(db):
    await a_world(db)
    assert await rotation.end_early(db, RotatingQuests(), "rank-s") is None
    with pytest.raises(rotation.RotationError):
        await rotation.end_early(db, RotatingQuests(), "nowhere")


async def test_a_deleted_team_takes_its_progress_with_it(db):
    await a_world(db, template("a"))
    team = await a_team(db)
    await rotation.offer(db, Hooks(), team.id, "rank-s")
    await rotation.forget_team(db, team.id)
    assert await db.scalar(select(func.count()).select_from(TeamSlotQuest)) == 0


# --- the calls ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    (seed_dir / "quest_slots.json").write_text(json.dumps([SLOT]))
    (seed_dir / "quest_templates.json").write_text(json.dumps([template("a"), template("b", "Lich")]))
    return Game(name="Test Game", seed_dir=seed_dir, rotating_quests=Hooks())


def test_the_calls_need_a_login_and_the_changing_ones_a_csrf_token(app_client):
    assert app_client.get("/api/teams/1/slot-quests").status_code == 401
    sign_in(app_client, "Mike")
    assert app_client.post("/api/teams/1/slot-quests/rank-s/offer").status_code == 403
    assert app_client.post("/api/admin/quest-slots/rank-s/end").status_code == 403


def test_a_player_asks_for_the_slots_quest_and_gets_the_same_one_until_it_rotates(app_client, later):
    start = wallclock.timestamp()
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    assert expect(app_client.get(f"/api/teams/{team}/slot-quests"), 200).json() == {"slots": []}, "nobody has asked yet: nothing is made by looking"
    offered = expect(app_client.post(f"/api/teams/{team}/slot-quests/rank-s/offer", headers=mike), 200).json()["slot"]
    assert (offered["key"], offered["guild"], offered["rank"]) == ("rank-s", "vanguard", 5)
    assert offered["team"]["status"] == "offered" and offered["quest"]["limit_seconds"] == 5 * DAY
    assert offered["quest"]["grace_ends"] == offered["quest"]["ends"] + 3 * DAY
    assert expect(app_client.post(f"/api/teams/{team}/slot-quests/rank-s/offer", headers=mike), 200).json()["slot"] == offered
    assert expect(app_client.get(f"/api/teams/{team}/slot-quests"), 200).json() == {"slots": [offered]}
    later(offered["quest"]["ends"] - start + 1)
    assert expect(app_client.get(f"/api/teams/{team}/slot-quests"), 200).json() == {"slots": []}, "rotated out, and nobody has asked about the new period"


def test_nobody_asks_for_another_players_teams_quest_or_a_slot_that_is_not_there(app_client):
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    expect(app_client.post(f"/api/teams/{team}/slot-quests/nowhere/offer", headers=mike), 404)
    zed = sign_in(app_client, "Zed")
    expect(app_client.get(f"/api/teams/{team}/slot-quests"), 404)
    expect(app_client.post(f"/api/teams/{team}/slot-quests/rank-s/offer", headers=zed), 404)


def test_asking_for_a_slot_quest_is_rate_limited(app_client, monkeypatch):
    monkeypatch.setattr(ratelimit, "SLOT_QUEST_BY_ACCOUNT", ratelimit.Limit("slot-quest", 2, 3600))
    mike = sign_in(app_client, "Mike")
    team = make_team(app_client, mike, "Vanguard")["id"]
    for _ in range(2):
        expect(app_client.post(f"/api/teams/{team}/slot-quests/rank-s/offer", headers=mike), 200)
    expect(app_client.post(f"/api/teams/{team}/slot-quests/rank-s/offer", headers=mike), 429)


def test_an_admin_ends_the_slots_quest_and_nobody_else_may(app_client):
    mike = sign_in(app_client, "Mike")
    expect(app_client.post("/api/admin/quest-slots/rank-s/end", headers=mike), 403)
    root = sign_in(app_client, "Root")
    in_app_db(app_client, lambda db: _make_admin(db, "Root"))
    expect(app_client.post("/api/admin/quest-slots/nowhere/end", headers=root), 404)
    mine = make_team(app_client, root, "Rootteam")["id"]
    first = expect(app_client.post(f"/api/teams/{mine}/slot-quests/rank-s/offer", headers=root), 200).json()["slot"]
    assert expect(app_client.post("/api/admin/quest-slots/rank-s/end", headers=root), 200).json() == {"ended": True}
    again = expect(app_client.post(f"/api/teams/{mine}/slot-quests/rank-s/offer", headers=root), 200).json()["slot"]
    assert again["quest"]["id"] != first["quest"]["id"] and again["team"]["status"] == "offered"
    assert expect(app_client.post("/api/admin/quest-slots/rank-s/end", headers=root), 200).json() == {"ended": True}


async def _make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


# --- the dialog tag ----------------------------------------------------------------------------------------------------

def test_the_slot_quest_tag_is_checked_when_the_dialog_is_parsed():
    from terraforma.npcs.script import ScriptError, parse
    parse("`slot_quest,rank-s,offer,end``slot_quest,rank-s,start,end``slot_quest,rank-s,finish_win,end``slot_quest,rank-s,finish_lose,end`done")
    for bad, why in (("`slot_quest,rank-s,offer`", "takes 3 parameters"), ("`slot_quest,rank-s,take,end`", "the action"),
                     ("`slot_quest,,offer,end`", "a quest slot key"), ("`slot_quest,rank-s,offer,nowhere`", "nowhere")):
        with pytest.raises(ScriptError, match=why):
            parse(bad)


async def test_a_dialog_offers_starts_and_finishes_a_slot_quest(db):
    await a_world(db, template("a"))
    p = await a_party(db)
    db.info["rotating_quests"] = hooks = Hooks()
    aria, bram = p.heroes[0], p.heroes[1]

    def says(action):
        return check(f"slot_quest,rank-s,{action},no")

    assert await npc_says(db, aria, says("finish_win")) == "no", "nothing taken"
    assert await npc_says(db, aria, says("offer")) == "yes"
    assert await npc_says(db, aria, says("finish_win")) == "no", "offered, not taken"
    assert await npc_says(db, aria, says("start")) == "yes"
    assert await npc_says(db, aria, says("start")) == "no", "already taken"
    assert await npc_says(db, aria, says("finish_lose")) == "yes" and hooks.penalties == [(p.teams[0], "rank-s", "poor_performance")]
    assert await npc_says(db, aria, says("offer")) == "no", "finished this period"
    assert await npc_says(db, bram, says("offer")) == "yes", "another team of the party has its own"
    assert await npc_says(db, bram, says("start")) == "yes"
    assert await npc_says(db, bram, says("finish_win")) == "yes" and await quests.completed(db, p.teams[1], "rank-s", 5) == 1


async def test_a_dialog_starting_without_an_offer_first_takes_the_quest_in_one_step(db):
    await a_world(db, template("a"))
    p = await a_party(db)
    db.info["rotating_quests"] = Hooks()
    assert await npc_says(db, p.heroes[0], check("slot_quest,rank-s,start,no")) == "yes"
    assert (await rotation.active(db, p.teams[0], "rank-s")) is not None


async def test_a_dialog_for_a_slot_that_holds_no_quest_goes_to_the_label(db):
    await a_world(db)  # (no templates, and the engine's hooks make none)
    p = await a_party(db)
    assert await npc_says(db, p.heroes[0], check("slot_quest,rank-s,offer,no")) == "no"


async def test_a_dialog_for_an_unknown_slot_is_an_error_the_dialog_reports(db):
    from terraforma.npcs.service import NpcError

    p = await a_party(db)
    with pytest.raises(NpcError, match="no such quest slot"):
        await npc_says(db, p.heroes[0], check("slot_quest,nowhere,offer,no"))
