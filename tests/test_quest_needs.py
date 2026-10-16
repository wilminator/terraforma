"""The quests the world needs: gaps in the players' progression are found per band of party strength and answered with existing monsters promoted to
bosses; a proposal waits for an admin or goes live, as the game decides. On every database."""

import json
from datetime import timedelta

import pytest
from sqlalchemy import func, select, update

from terraforma import housekeeping
from terraforma.challenge import service as challenge
from terraforma.content.loader import load_content
from terraforma.content.models import Monster
from terraforma.content.schema import ContentError
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.models import Account, World
from terraforma.quests import bosses, needs, rotation
from terraforma.quests import service as quests
from terraforma.quests.hooks import Band, Needs
from terraforma.quests.needs_models import QuestProposal
from terraforma.quests.weekly_models import QuestTemplate
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_towns import sign_in
from .test_weekly_quests import SEED as TEAM_SEED
from .test_weekly_quests import a_team

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

DAY = 24 * 60 * 60

SEED = {
    **TEAM_SEED,
    "abilities": [{"key": "roar", "name": "Roar", "kind": "skill"}],
    "personalities": [{"key": "plain", "name": "Plain"}],
    "monsters": [
        {"key": "rat", "name": "Rat", "personality": "plain", "stats": {"HP": 10}},
        {"key": "wolf", "name": "Wolf", "personality": "plain", "stats": {"HP": 40, "Strength": 10}},
        {"key": "ogre", "name": "Ogre", "personality": "plain", "stats": {"HP": 100, "Strength": 30}},
        {"key": "dragon", "name": "Dragon", "personality": "plain", "stats": {"HP": 300, "Strength": 100}},
    ],
    "quest_slots": [{"key": "hunt", "name": "Hunt", "min_days": 10, "max_days": 20, "limit_days": 5},
                    {"key": "other", "name": "Other", "min_days": 10, "max_days": 20, "limit_days": 5}],
}


class Flat(Rules):
    """Strength is easy to read here: ten times a fighter's stats added up (every monster has 90 in its other stats: a rat is 190, a wolf 580, an ogre 1380, a dragon 4080)."""

    def pxp(self, fighter):
        return sum(fighter.base.values()) * 10


class Wanted(Needs):
    def __init__(self, *bands, approval=True, gang=1, picks=None, added=None):
        self.listed, self.approval, self.gang, self.picks, self.added = list(bands), approval, gang, picks, added or {}

    def bands(self):
        return self.listed

    def needs_approval(self, band):
        return self.approval

    def gang_size(self, rng, band):
        return self.gang

    def pick(self, rng, band, candidates):
        return self.picks

    def extras(self, rng, band, monster):
        return self.added


async def a_world(db, *templates):
    await load_content(db, {**SEED, "quest_templates": list(templates)})
    await ensure_start(db)
    await db.execute(update(World).values(seed=4242))


async def a_strength(db, name="Vanguard", level=1):
    """A team with a hero, and its strength under the test rules."""
    team = await a_team(db, name, level=level)
    return team, (await needs.team_strengths(db, Flat()))[team.id]


def band_around(strength, **more):
    return Band("wide", "hunt", pxp_min=max(1, strength // 2), pxp_max=strength * 4, **more)


def window(key="old", low=0, high=0, slot="hunt"):
    return {"key": key, "name": "Old hunt", "slot": slot, "pxp_min": low, "pxp_max": high}


# --- finding a gap -----------------------------------------------------------------------------------------------------

async def test_the_default_game_lists_no_bands_so_nothing_is_made(db):
    await a_world(db)
    await a_strength(db)
    assert await needs.run(db, Needs(), Flat()) == []
    assert await db.scalar(select(func.count()).select_from(QuestProposal)) == 0


async def test_a_band_with_no_quest_fitting_it_gets_a_boss_scaled_to_its_target(db):
    await a_world(db)
    _, strength = await a_strength(db)
    band = band_around(strength)
    made = await needs.run(db, Wanted(band), Flat())
    assert len(made) == 1
    proposal = made[0]
    assert (proposal.band, proposal.slot, proposal.reason, proposal.status, proposal.template_id) == ("wide", "hunt", "no_fit", "proposed", None)
    assert proposal.name.startswith("Hunt: ") and proposal.spec["band"] == "wide" and proposal.spec["reason"] == "no_fit"
    [boss] = proposal.spec["bosses"]
    assert boss["monster"] in ("rat", "wolf", "ogre", "dragon") and boss["extras"] == {}
    target = band.pxp_max
    assert target <= boss["pxp"] < target * 1.02, "scaled up to the band's ceiling, and not much past it"


async def test_a_boss_is_scaled_from_a_monster_that_is_neither_too_weak_nor_too_strong(db):
    await a_world(db)
    _, strength = await a_strength(db)
    band = Band("wide", "hunt", pxp_min=1, pxp_max=1000)
    for _ in range(5):
        [proposal] = await needs.run(db, Wanted(band), Flat())
        [boss] = proposal.spec["bosses"]
        assert boss["monster"] in ("rat", "wolf"), "the floor is an eighth of 1000 (125) and the ogre (1380) is over the ceiling"
        await needs.reject(db, proposal.id)
    assert strength > 0


async def test_the_pick_replays_from_the_worlds_seed_and_changes_with_each_proposal(db):
    await a_world(db)
    _, strength = await a_strength(db)
    band = band_around(strength)
    [first] = await needs.run(db, Wanted(band), Flat())
    kept = first.spec
    await needs.reject(db, first.id)
    await db.delete(first)
    await db.flush()
    [again] = await needs.run(db, Wanted(band), Flat())
    assert again.spec == kept, "same seed, band and count of earlier proposals"


async def test_a_band_has_one_open_proposal_and_a_rejected_one_may_be_answered_again(db):
    await a_world(db)
    _, strength = await a_strength(db)
    wanted = Wanted(band_around(strength))
    [first] = await needs.run(db, wanted, Flat())
    assert await needs.run(db, wanted, Flat()) == [], "the gap already has an answer waiting"
    await needs.reject(db, first.id)
    [second] = await needs.run(db, wanted, Flat())
    assert second.id != first.id and await db.scalar(select(func.count()).select_from(QuestProposal)) == 2


async def test_a_quest_of_the_slot_meant_for_the_bands_strength_closes_the_gap(db):
    await a_world(db, window("fits", 100, 100_000))
    _, strength = await a_strength(db)
    assert await needs.run(db, Wanted(band_around(strength)), Flat()) == []


async def test_a_quest_that_says_nothing_about_strength_or_belongs_to_another_slot_or_is_meant_for_other_teams_leaves_the_gap(db):
    await a_world(db, window("unsaid"), window("elsewhere", 1, 1_000_000, slot="other"), window("weak", 1, 5))
    _, strength = await a_strength(db)
    [proposal] = await needs.run(db, Wanted(band_around(strength)), Flat())
    assert proposal.reason == "no_fit"


async def test_a_band_that_holds_too_few_teams_is_not_judged(db):
    await a_world(db)
    _, strength = await a_strength(db)
    assert await needs.run(db, Wanted(Band("far", "hunt", pxp_min=strength * 10)), Flat()) == []
    assert await needs.run(db, Wanted(band_around(strength, min_teams=2)), Flat()) == []
    await a_strength(db, "Rearguard")
    assert len(await needs.run(db, Wanted(band_around(strength, min_teams=2)), Flat())) == 1


async def test_a_band_where_nobody_completes_quests_is_stalled_and_one_where_someone_does_is_not(db, later):
    await a_world(db, window("fits", 100, 100_000))
    first, strength = await a_strength(db)
    second, _ = await a_strength(db, "Rearguard")
    band = band_around(strength, stall_seconds=10 * DAY)
    assert await needs.run(db, Wanted(band), Flat()) == [], "the teams are new"
    later(11 * DAY)
    await quests.complete(db, second.id, "weekly", 1)
    assert await needs.run(db, Wanted(band), Flat()) == [], "one team progressed, so the band is moving"
    later(25 * DAY)
    [proposal] = await needs.run(db, Wanted(band), Flat())
    assert (proposal.reason, proposal.status) == ("stalled", "proposed")
    assert first.id != second.id


async def test_stalls_are_not_looked_for_unless_the_band_says_so(db, later):
    await a_world(db, window("fits", 100, 100_000))
    _, strength = await a_strength(db)
    later(365 * DAY)
    assert await needs.run(db, Wanted(band_around(strength)), Flat()) == []


# --- the boss -------------------------------------------------------------------------------------------------------------

async def test_a_band_with_no_ceiling_aims_at_half_as_much_again_as_its_floor(db):
    await a_world(db)
    assert Needs().target(Band("open", "hunt", pxp_min=1000)) == 1500
    _, strength = await a_strength(db)
    band = Band("open", "hunt", pxp_min=strength)
    await db.execute(update(Monster).where(Monster.key != "rat").values(active=False))
    [proposal] = await needs.run(db, Wanted(band), Flat())
    assert strength * 3 // 2 <= proposal.spec["bosses"][0]["pxp"] < strength * 3 // 2 * 1.02


async def test_a_gang_shares_the_target_between_its_monsters(db):
    await a_world(db)
    _, strength = await a_strength(db)
    band = Band("wide", "hunt", pxp_min=1, pxp_max=3000)
    [proposal] = await needs.run(db, Wanted(band, gang=3), Flat())
    gang = proposal.spec["bosses"]
    assert len(gang) == 3 and all(1000 <= each["pxp"] < 1030 for each in gang)
    assert strength > 0


async def test_the_game_may_pick_the_monsters_and_add_abilities_to_a_boss(db):
    await a_world(db)
    _, strength = await a_strength(db)
    wanted = Wanted(band_around(strength), picks=["ogre"], added={"abilities": ["roar"], "flavour": "cold"})
    [proposal] = await needs.run(db, wanted, Flat())
    [boss] = proposal.spec["bosses"]
    assert (boss["monster"], boss["extras"]) == ("ogre", {"abilities": ["roar"], "flavour": "cold"})
    items, abilities = await bosses.tables(db)
    ogre = await db.scalar(select(Monster).where(Monster.key == "ogre"))
    fighter = bosses.boss_combatant(ogre, boss, items, abilities, Flat())
    assert [each.key for each in fighter.abilities] == ["roar"] and fighter.base["HP"] == round(100 * boss["scale"])
    assert Flat().pxp(fighter) == boss["pxp"]


@pytest.mark.parametrize("picks", [[], ["unicorn"], ["rat"] * 7])
async def test_a_game_that_picks_monsters_the_world_does_not_have_is_refused(db, picks):
    await a_world(db)
    _, strength = await a_strength(db)
    with pytest.raises(bosses.BossError):
        await needs.run(db, Wanted(band_around(strength), picks=picks), Flat())


async def test_no_monster_the_world_has_fits_a_band_it_is_left_alone(db):
    await a_world(db)
    await a_strength(db)
    far = Band("far", "hunt", pxp_min=1, pxp_max=10_000_000)
    assert await needs.run(db, Wanted(far), Flat()) == []
    assert await db.scalar(select(func.count()).select_from(QuestProposal)) == 0


async def test_scaling_stops_at_the_most_a_monster_may_be_multiplied(db):
    await a_world(db)
    items, abilities = await bosses.tables(db)
    rat = await db.scalar(select(Monster).where(Monster.key == "rat"))
    assert bosses.scale_for(Flat(), rat, items, abilities, 50) == 1.0, "already strong enough"
    assert bosses.scale_for(Flat(), rat, items, abilities, 100 * 61) is None
    assert bosses.scale_for(Flat(), rat, items, abilities, 1000) == pytest.approx(9.1, abs=0.1)


# --- the proposal ----------------------------------------------------------------------------------------------------------

async def test_approving_a_proposal_makes_a_template_of_the_slot_that_closes_the_gap(db):
    await a_world(db)
    _, strength = await a_strength(db)
    wanted = Wanted(band_around(strength))
    [proposal] = await needs.run(db, wanted, Flat())
    live = await needs.approve(db, proposal.id)
    template = await db.get(QuestTemplate, live.template_id)
    assert (live.status, template.key, template.source, template.slot, template.active) == ("live", f"need-{proposal.id}", "needs", "hunt", True)
    assert (template.pxp_min, template.pxp_max, template.spec, template.name) == (proposal.pxp_min, proposal.pxp_max, proposal.spec, proposal.name)
    assert await needs.run(db, wanted, Flat()) == [], "the band has its quest now, and the proposal is still open"
    slot = await rotation.get_slot(db, "hunt")
    quest = await rotation.current(db, rotation.RotatingQuests() if False else __import__("terraforma.quests.hooks", fromlist=["x"]).RotatingQuests(), slot)
    assert (quest.source, quest.template_id, quest.spec) == ("template", template.id, proposal.spec), "the slot offers it from its period"
    with pytest.raises(needs.NeedsError):
        await needs.approve(db, proposal.id)
    with pytest.raises(needs.NeedsError):
        await needs.reject(db, proposal.id)


async def test_a_game_that_wants_no_approval_has_the_quest_live_at_once(db):
    await a_world(db)
    _, strength = await a_strength(db)
    [proposal] = await needs.run(db, Wanted(band_around(strength), approval=False), Flat())
    assert proposal.status == "live" and proposal.template_id is not None


async def test_a_quest_taken_out_of_its_slot_leaves_the_gap_to_be_answered_again(db):
    await a_world(db)
    _, strength = await a_strength(db)
    wanted = Wanted(band_around(strength), approval=False)
    [first] = await needs.run(db, wanted, Flat())
    with pytest.raises(needs.NeedsError):
        await needs.retire(db, 999)
    retired = await needs.retire(db, first.id)
    assert retired.status == "retired" and (await db.get(QuestTemplate, first.template_id)).active is False
    [second] = await needs.run(db, wanted, Flat())
    assert second.id != first.id and second.template_id != first.template_id
    with pytest.raises(needs.NeedsError):
        await needs.retire(db, first.id)


async def test_a_proposal_that_was_not_approved_cannot_be_retired(db):
    await a_world(db)
    _, strength = await a_strength(db)
    [proposal] = await needs.run(db, Wanted(band_around(strength)), Flat())
    with pytest.raises(needs.NeedsError, match="only a live quest"):
        await needs.retire(db, proposal.id)


async def test_proposals_are_listed_oldest_first_by_stage(db):
    await a_world(db)
    _, strength = await a_strength(db)
    wanted = Wanted(band_around(strength))
    [first] = await needs.run(db, wanted, Flat())
    await needs.reject(db, first.id)
    [second] = await needs.run(db, wanted, Flat())
    assert [each.id for each in await needs.proposals(db)] == [first.id, second.id]
    assert [each.id for each in await needs.proposals(db, "proposed")] == [second.id]
    assert [each.id for each in await needs.proposals(db, after=first.id)] == [second.id]


# --- housekeeping and the seed -------------------------------------------------------------------------------------------

async def test_housekeeping_runs_the_needs_step_with_the_games_hooks_and_does_nothing_without_them(db):
    await a_world(db)
    _, strength = await a_strength(db)
    assert await housekeeping.quest_needs(db, 0, None, Flat()) == 0
    db.info["needs"] = Wanted(band_around(strength))
    assert await housekeeping.quest_needs(db, 0, None, Flat()) == 1
    assert await housekeeping.quest_needs(db, 0, None, Flat()) == 0, "one open proposal per band"


async def test_templates_may_say_what_strength_they_are_for_and_a_backwards_range_is_refused(db):
    await a_world(db, window("fits", 100, 5000))
    row = await db.scalar(select(QuestTemplate).where(QuestTemplate.key == "fits"))
    assert (row.pxp_min, row.pxp_max, row.slot) == (100, 5000, "hunt")
    with pytest.raises(ContentError):
        await load_content(db, {**SEED, "quest_templates": [window("bad", 500, 100)]})


# --- the calls -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for name, rows in (("jobs", SEED["jobs"]), ("quest_slots", SEED["quest_slots"])):
        (seed_dir / f"{name}.json").write_text(json.dumps(rows))
    return Game(name="Test Game", seed_dir=seed_dir, rules=Flat(), needs=Wanted())


async def _proposal(db):
    proposal = QuestProposal(band="wide", slot="hunt", reason="no_fit", name="Hunt: wolf", spec={"bosses": [], "band": "wide", "reason": "no_fit"})
    db.add(proposal)
    await db.flush()
    return proposal.id


async def _make_admin(db, name):
    await challenge.set_admin(db, await db.scalar(select(Account.id).where(Account.username == name)))


def test_the_calls_need_a_login_an_admin_and_the_changing_ones_a_csrf_token(app_client):
    assert app_client.get("/api/admin/quest-proposals").status_code == 401
    mike = sign_in(app_client, "Mike")
    assert app_client.get("/api/admin/quest-proposals").status_code == 403
    for url in ("/api/admin/quest-proposals/1/approve", "/api/admin/quest-proposals/1/reject", "/api/admin/quest-proposals/1/retire", "/api/admin/quest-needs/run"):
        assert app_client.post(url).status_code == 403, url
        assert app_client.post(url, headers=mike).status_code == 403, "a player is not an admin"


def test_an_admin_looks_over_the_proposals_and_approves_rejects_and_retires_them(app_client):
    root = sign_in(app_client, "Root")
    in_app_db(app_client, lambda db: _make_admin(db, "Root"))
    first, second = in_app_db(app_client, _proposal), in_app_db(app_client, _two)
    listing = expect(app_client.get("/api/admin/quest-proposals"), 200).json()["proposals"]
    assert [each["id"] for each in listing] == [first, second] and listing[0]["status"] == "proposed"
    assert [each["id"] for each in expect(app_client.get("/api/admin/quest-proposals?status=proposed&after=1"), 200).json()["proposals"]] == [second]
    expect(app_client.get("/api/admin/quest-proposals?status=bogus"), 422)
    live = expect(app_client.post(f"/api/admin/quest-proposals/{first}/approve", headers=root), 200).json()["proposal"]
    assert live["status"] == "live" and live["template_id"] is not None
    expect(app_client.post(f"/api/admin/quest-proposals/{first}/approve", headers=root), 422)
    assert expect(app_client.post(f"/api/admin/quest-proposals/{second}/reject", headers=root), 200).json()["proposal"]["status"] == "rejected"
    assert expect(app_client.post(f"/api/admin/quest-proposals/{first}/retire", headers=root), 200).json()["proposal"]["status"] == "retired"
    expect(app_client.post("/api/admin/quest-proposals/999/approve", headers=root), 404)


async def _two(db):
    proposal = QuestProposal(band="narrow", slot="hunt", reason="stalled", name="Hunt: ogre", spec={"bosses": [], "band": "narrow", "reason": "stalled"})
    db.add(proposal)
    await db.flush()
    return proposal.id


def test_an_admin_runs_the_gap_check_now_and_a_game_that_lists_no_bands_makes_nothing(app_client):
    root = sign_in(app_client, "Root")
    in_app_db(app_client, lambda db: _make_admin(db, "Root"))
    assert expect(app_client.post("/api/admin/quest-needs/run", headers=root), 200).json() == {"proposals": []}
