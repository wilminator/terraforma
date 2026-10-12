"""Fight tags and retention: how a finished fight is scored, the tags a team holds, an alliance's lists, the fights kept by hand, and
the housekeeping that deletes the rest. On every database."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.alliances import service as alliances
from terraforma.alliances.hooks import Alliances
from terraforma.fights import live, store
from terraforma.fights.combatant import Combatant
from terraforma.fights.events import Event, EventType
from terraforma.fights.fight import build_fight
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.fighttags import scoring, service
from terraforma.fighttags.models import AllianceKept, AllianceTop, FightTeam, TeamKept, TeamTag
from terraforma.heroes import service as heroes
from terraforma.housekeeping import old_unkept_fights

from .test_live_fights import RULES, a_team, attack

# --- the scoring (pure) ------------------------------------------------------------------------------------------------


class Flat(Rules):
    """Everyone is worth their max HP, so the scores can be read off."""

    def pxp(self, fighter):
        return fighter.base["HP"]


FLAT = Flat()


def body(name, hp, charid=None, hurt=0):
    return Combatant(name, {"HP": hp, "MP": 0}, {"HP": hp - hurt, "MP": 0}, charid=charid)


def turn(address):
    return Event(EventType.TURN, address)


def died(address, overkill):
    return Event(EventType.DIED, (*address, 10, overkill))


def pve(hero_hp=100, foe_hp=50):
    """Team 7 (hero 101) against one monster; the monster is down."""
    hero, foe = body("Aria", hero_hp, 101), body("Rat", foe_hp, hurt=foe_hp)
    fight = build_fight({0: {0: [hero]}, 1: {0: [foe]}})
    fight.parties[0].teams = {7: [101]}
    fight.over = True
    return fight, hero, foe


def test_a_kill_credits_the_overkill_to_the_team_of_the_fighter_whose_turn_it_is():
    fight, *_ = pve()
    events = [turn((0, 0, 0)), died((1, 0, 0), 30)]
    assert scoring.overkill_by_team(fight, FLAT, events) == {7: 30}


def test_overkill_adds_up_over_the_kills_and_a_kill_by_a_monster_credits_no_team():
    fight, *_ = pve()
    fight.parties[1].groups[0].characters[1] = body("Bat", 10)
    events = [turn((0, 0, 0)), died((1, 0, 0), 30), turn((0, 0, 0)), died((1, 0, 1), 5), turn((1, 0, 0)), died((0, 0, 0), 99)]
    assert scoring.overkill_by_team(fight, FLAT, events) == {7: 35}


def test_a_death_outside_anyones_turn_or_of_a_fighter_on_the_same_party_is_credited_to_nobody():
    fight, *_ = pve()
    fight.parties[0].groups[0].characters[1] = body("Bob", 10, 102)
    fight.parties[0].teams = {7: [101, 102]}
    events = [died((1, 0, 0), 30), turn((0, 0, 0)), died((0, 0, 1), 8), Event(EventType.ROUND_END, ()), died((1, 0, 0), 40)]
    assert scoring.overkill_by_team(fight, FLAT, events) == {}


def test_a_winning_team_scores_overkill_over_its_pxp_and_underdog_as_the_foes_pxp_over_its_partys():
    fight, *_ = pve(hero_hp=100, foe_hp=50)
    [result] = scoring.team_results(fight, FLAT, [turn((0, 0, 0)), died((1, 0, 0), 30)])
    assert (result.team_id, result.pvp, result.won) == (7, False, True)
    assert result.overkill == pytest.approx(0.3) and result.underdog == pytest.approx(0.5)
    assert result.kinds() == (scoring.PVE_OVERKILL, scoring.PVE_UNDERDOG)
    assert result.score(scoring.PVE_UNDERDOG) == pytest.approx(0.5) and result.score(scoring.PVP_UNDERDOG) == 0, "the other sort scores nothing"


def test_beating_a_stronger_party_is_a_better_underdog_than_beating_a_weaker_one():
    strong, *_ = pve(hero_hp=100, foe_hp=300)
    weak, *_ = pve(hero_hp=100, foe_hp=20)
    assert scoring.team_results(strong, FLAT, [])[0].underdog == pytest.approx(3.0)
    assert scoring.team_results(weak, FLAT, [])[0].underdog == pytest.approx(0.2)


def test_a_team_that_did_not_win_scores_nothing():
    fight, hero, foe = pve()
    foe.current["HP"], hero.current["HP"] = 10, 0  # the monster stands and the hero is down
    [result] = scoring.team_results(fight, FLAT, [turn((1, 0, 0)), died((0, 0, 0), 5)])
    assert (result.won, result.overkill, result.underdog) == (False, 0, 0)


def test_a_party_that_fled_is_beaten_but_a_team_that_fled_has_not_won():
    fight, hero, foe = pve()
    foe.current["HP"], foe.fled = 10, True  # the monster fled
    assert scoring.team_results(fight, FLAT, [])[0].won is True
    foe.fled, hero.fled = False, True  # now the monster stands and the hero fled
    assert scoring.team_results(fight, FLAT, [])[0].won is False


def test_a_fight_against_another_players_team_is_pvp_and_both_teams_are_scored_but_only_the_winner_wins():
    one, two = body("Aria", 100, 101), body("Zed", 100, 201, hurt=100)
    fight = build_fight({0: {0: [one]}, 1: {0: [two]}})
    fight.parties[0].teams, fight.parties[1].teams = {7: [101]}, {8: [201]}
    results = {result.team_id: result for result in scoring.team_results(fight, FLAT, [turn((0, 0, 0)), died((1, 0, 0), 20)])}
    assert results[7].pvp and results[8].pvp and results[7].won and not results[8].won
    assert results[7].overkill == pytest.approx(0.2) and results[7].kinds() == (scoring.PVP_OVERKILL, scoring.PVP_UNDERDOG)
    assert results[8].underdog == 0


def test_a_party_with_no_enemy_wins_nothing():
    hero = body("Aria", 100, 101)
    fight = build_fight({0: {0: [hero]}})
    fight.parties[0].teams = {7: [101]}
    assert scoring.team_results(fight, FLAT, [])[0].won is False


# --- the tags (database) -----------------------------------------------------------------------------------------------




async def a_fight(db, hero, *, age_days=0, finished=True):
    record = FightRecord(guid=uuid.uuid4().hex, initial_state={}, map_id=hero.map_id, finished=finished)
    db.add(record)
    await db.flush()
    if age_days:
        record.updated_at = wallclock.now() - timedelta(days=age_days)
        await db.flush()
    return record


async def scored(db, record, team, *, won=True, pvp=False, overkill=0.0, underdog=0.0):
    db.add(FightTeam(fight_id=record.id, team_id=team.id, party=0, pvp=pvp, won=won, overkill=overkill, underdog=underdog))
    await db.flush()


async def tags_of(db, team):
    return {row.kind: (row.fight_id, row.score) for row in (await db.scalars(select(TeamTag).where(TeamTag.team_id == team.id))).all()}


def result(team, *, pvp=False, won=True, overkill=0.0, underdog=0.0):
    return scoring.TeamResult(team_id=team.id, party=0, pvp=pvp, won=won, overkill=overkill, underdog=underdog)


@pytest.mark.anyio
async def test_a_fight_played_to_its_end_is_scored_for_the_winning_team_and_takes_its_underdog_tag(db):
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], RULES)
    while not record.finished:
        await attack(db, record, mike)
        await live.resolve_round(db, record, RULES)
    row = await db.get(FightTeam, (record.id, team.id))
    assert (row.won, row.pvp) == (True, False) and row.underdog > 0
    held = await tags_of(db, team)
    assert held[scoring.PVE_UNDERDOG] == (record.id, row.underdog)
    assert (scoring.PVE_OVERKILL in held) == (row.overkill > 0), "a fight takes a tag only for a score above nothing"
    assert not [kind for kind in held if kind.startswith("pvp_")]


@pytest.mark.anyio
async def test_scoring_a_fight_again_changes_nothing(db):
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], RULES)
    while not record.finished:
        await attack(db, record, mike)
        await live.resolve_round(db, record, RULES)
    before = await tags_of(db, team)
    fight, _ = await store.load_state(db, record, RULES)
    assert await service.record_fight(db, record, fight, RULES, []) == []
    assert await tags_of(db, team) == before


@pytest.mark.anyio
async def test_a_better_score_takes_the_tag_and_an_equal_or_worse_one_does_not(db):
    hero, _mike, team = await a_team(db)
    first, second, third, fourth = [await a_fight(db, hero) for _ in range(4)]
    for record, score in ((first, 0.5), (second, 0.9), (third, 0.9), (fourth, 0.1)):
        await service._take_team_tag(db, team.id, scoring.PVE_UNDERDOG, record.id, score)
    assert await tags_of(db, team) == {scoring.PVE_UNDERDOG: (second.id, 0.9)}


@pytest.mark.anyio
async def test_pve_and_pvp_tags_are_kept_apart_and_a_team_holds_up_to_four(db):
    hero, _mike, team = await a_team(db)
    record = await a_fight(db, hero)
    for kind in scoring.KINDS:
        await service._take_team_tag(db, team.id, kind, record.id, 1.0)
    assert sorted(await tags_of(db, team)) == sorted(scoring.KINDS)


@pytest.mark.anyio
async def test_a_pvp_fight_judged_thrown_takes_no_pvp_tag_but_is_still_recorded(db):
    class Thrown(Flat):
        def thrown_fight(self, fight):
            return True

    hero, _mike, team = await a_team(db)
    duel = build_fight({0: {0: [body("Aria", 100, 101)]}, 1: {0: [body("Zed", 100, 201, hurt=100)]}})
    duel.parties[0].teams, duel.parties[1].teams = {team.id: [101]}, {team.id + 100: [201]}
    events = [turn((0, 0, 0)), died((1, 0, 0), 20)]
    record = await a_fight(db, hero)
    await service.record_fight(db, record, duel, Thrown(), events)
    assert await tags_of(db, team) == {}, "a thrown PvP fight takes no tag"
    assert (await db.get(FightTeam, (record.id, team.id))).won is True, "but it is recorded"
    honest = await a_fight(db, hero)
    await service.record_fight(db, honest, duel, FLAT, events)
    assert sorted(await tags_of(db, team)) == [scoring.PVP_OVERKILL, scoring.PVP_UNDERDOG]


@pytest.mark.anyio
async def test_the_thrown_fight_rule_does_not_stop_a_pve_fight_taking_its_tags(db):
    class Thrown(Flat):
        def thrown_fight(self, fight):
            return True

    hero, _mike, team = await a_team(db)
    fight, *_ = pve()
    fight.parties[0].teams = {team.id: [101]}
    record = await a_fight(db, hero)
    await service.record_fight(db, record, fight, Thrown(), [turn((0, 0, 0)), died((1, 0, 0), 30)])
    assert sorted(await tags_of(db, team)) == [scoring.PVE_OVERKILL, scoring.PVE_UNDERDOG]


# --- alliances ---------------------------------------------------------------------------------------------------------

async def an_alliance(db, mike, team):
    return await alliances.found(db, Alliances(), team, "Iron Pact")


@pytest.mark.anyio
async def test_an_alliance_lists_its_teams_best_fights_per_kind_and_drops_the_worst_beyond_its_size(db):
    class Small(Rules):
        alliance_top_size = 3

    hero, mike, team = await a_team(db)
    pact = await an_alliance(db, mike, team)
    records = [await a_fight(db, hero) for _ in range(5)]
    for record, score in zip(records, (0.2, 0.9, 0.5, 0.7, 0.3)):
        await service._enter_alliance_tops(db, Small(), team.id, scoring.PVE_UNDERDOG, record.id, score)
    rows = (await db.scalars(select(AllianceTop).where(AllianceTop.alliance_id == pact.id).order_by(AllianceTop.score.desc()))).all()
    assert [(row.fight_id, row.score) for row in rows] == [(records[1].id, 0.9), (records[3].id, 0.7), (records[2].id, 0.5)]
    view = await service.alliance_view(db, Small(), pact.id)
    assert [entry["score"] for entry in view["top"][scoring.PVE_UNDERDOG]] == [0.9, 0.7, 0.5] and view["top"][scoring.PVP_OVERKILL] == []


@pytest.mark.anyio
async def test_a_team_in_no_alliance_adds_to_no_list_and_a_fight_appears_once_in_a_list(db):
    hero, mike, team = await a_team(db)
    record = await a_fight(db, hero)
    await service._enter_alliance_tops(db, RULES, team.id, scoring.PVE_UNDERDOG, record.id, 0.5)
    assert (await db.scalars(select(AllianceTop))).all() == []
    pact = await an_alliance(db, mike, team)
    await service._enter_alliance_tops(db, RULES, team.id, scoring.PVE_UNDERDOG, record.id, 0.5)
    await service._enter_alliance_tops(db, RULES, team.id, scoring.PVE_UNDERDOG, record.id, 0.8)
    rows = (await db.scalars(select(AllianceTop).where(AllianceTop.alliance_id == pact.id))).all()
    assert [(row.fight_id, row.score) for row in rows] == [(record.id, 0.8)]


# --- keeping fights by hand ---------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_team_keeps_a_finished_fight_it_was_in_in_one_of_its_slots_and_lets_it_go(db):
    class Two(Rules):
        team_retention_slots = 2

    hero, _mike, team = await a_team(db)
    records = [await a_fight(db, hero) for _ in range(3)]
    for record in records:
        await scored(db, record, team)
    stranger = await a_fight(db, hero)
    await service.keep(db, Two(), team.id, records[0].id)
    await service.keep(db, Two(), team.id, records[0].id)  # again: no new slot used
    await service.keep(db, Two(), team.id, records[1].id)
    with pytest.raises(service.TagError, match="slots are used"):
        await service.keep(db, Two(), team.id, records[2].id)
    with pytest.raises(service.NotFound):
        await service.keep(db, Two(), team.id, stranger.id)
    view = await service.team_view(db, Two(), team.id)
    assert [entry["fight_id"] for entry in view["kept"]] == [records[1].id, records[0].id] and view["slots"] == 2
    await service.release(db, team.id, records[0].id)
    await service.keep(db, Two(), team.id, records[2].id)
    assert {row.fight_id for row in (await db.scalars(select(TeamKept))).all()} == {records[1].id, records[2].id}


@pytest.mark.anyio
async def test_an_alliance_keeps_a_fight_its_team_was_in_and_it_stays_when_that_team_leaves(db):
    class One(Rules):
        alliance_retention_slots = 1

    hero, mike, team = await a_team(db)
    pact = await an_alliance(db, mike, team)
    first, second = await a_fight(db, hero), await a_fight(db, hero)
    await scored(db, first, team)
    await scored(db, second, team)
    await service.alliance_keep(db, One(), pact.id, team.id, first.id)
    with pytest.raises(service.TagError, match="slots are used"):
        await service.alliance_keep(db, One(), pact.id, team.id, second.id)
    other = await heroes.create_team(db, mike, "Other")
    with pytest.raises(service.NotFound):
        await service.alliance_keep(db, One(), pact.id, other.id, second.id)
    row = await db.get(AllianceKept, (pact.id, first.id))
    assert row.team_id == team.id
    await service.alliance_release(db, pact.id, first.id)
    assert await db.get(AllianceKept, (pact.id, first.id)) is None


# --- deleting what nobody holds ----------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_finished_fight_nothing_holds_is_deleted_after_the_grace_with_what_belongs_to_it(db):
    hero, _mike, team = await a_team(db)
    old = await a_fight(db, hero, age_days=31)
    db.add(FightParticipant(fight_id=old.id, party=0, group_index=0, character=0, name="Aria", hero_id=hero.id))
    db.add(FightActionRecord(fight_id=old.id, sequence=1, commands=[], events=[], previous_hash="", hash=""))
    await scored(db, old, team)
    recent = await a_fight(db, hero, age_days=29)
    running = await a_fight(db, hero, age_days=90, finished=False)
    assert await service.sweep(db, RULES, wallclock.timestamp()) == 1
    assert await db.get(FightRecord, old.id) is None and await db.get(FightRecord, recent.id) is not None and await db.get(FightRecord, running.id) is not None
    assert (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == old.id))).all() == []
    assert await db.get(FightTeam, (old.id, team.id)) is None


@pytest.mark.anyio
async def test_a_fight_a_tag_a_list_or_a_slot_holds_is_kept_however_old(db):
    hero, mike, team = await a_team(db)
    pact = await an_alliance(db, mike, team)
    tagged, listed, kept, shared, loose = [await a_fight(db, hero, age_days=100) for _ in range(5)]
    await service._take_team_tag(db, team.id, scoring.PVE_OVERKILL, tagged.id, 1.0)
    await service._enter_alliance_tops(db, RULES, team.id, scoring.PVE_UNDERDOG, listed.id, 1.0)
    await scored(db, kept, team)
    await service.keep(db, RULES, team.id, kept.id)
    await scored(db, shared, team)
    await service.alliance_keep(db, RULES, pact.id, team.id, shared.id)
    assert await service.sweep(db, RULES, wallclock.timestamp()) == 1
    assert await db.get(FightRecord, loose.id) is None
    for record in (tagged, listed, kept, shared):
        assert await db.get(FightRecord, record.id) is not None


@pytest.mark.anyio
async def test_the_housekeeping_job_uses_the_games_grace_and_a_grace_of_zero_keeps_everything(db):
    class Short(Rules):
        fight_keep_days = 5

    class Forever(Rules):
        fight_keep_days = 0

    hero, _mike, _team = await a_team(db)
    fight = await a_fight(db, hero, age_days=10)
    assert await old_unkept_fights(db, wallclock.timestamp(), None, Forever()) == 0
    assert await old_unkept_fights(db, wallclock.timestamp(), None, Short()) == 1
    assert await db.get(FightRecord, fight.id) is None


@pytest.mark.anyio
async def test_every_table_that_points_at_a_fight_is_dealt_with_when_one_is_deleted(db):
    from terraforma.db.base import Base
    import terraforma.models  # noqa: F401  (every model registered)

    pointing = {table.name for table in Base.metadata.tables.values() if any(key.column.table.name == "fights" for key in table.foreign_keys)}
    held_by_tags = {"team_tags", "team_kept", "alliance_tops", "alliance_kept"}
    handled = set(service.DELETED_WITH_THE_FIGHT) | set(service.LET_GO) | held_by_tags
    assert pointing <= handled, f"a table links to fights and the sweep does not know it: {sorted(pointing - handled)}"


@pytest.mark.anyio
async def test_a_deleted_team_takes_its_tags_slots_and_results_and_a_disbanded_alliance_its_lists(db):
    hero, mike, team = await a_team(db)
    pact = await an_alliance(db, mike, team)
    record = await a_fight(db, hero)
    await scored(db, record, team)
    await service._take_team_tag(db, team.id, scoring.PVE_OVERKILL, record.id, 1.0)
    await service.keep(db, RULES, team.id, record.id)
    await service._enter_alliance_tops(db, RULES, team.id, scoring.PVE_OVERKILL, record.id, 1.0)
    await service.alliance_keep(db, RULES, pact.id, team.id, record.id)
    await heroes.delete_team(db, mike, team.id)
    assert (await db.scalars(select(TeamTag))).all() == [] and (await db.scalars(select(TeamKept))).all() == []
    assert (await db.scalars(select(FightTeam))).all() == []
    assert (await db.scalars(select(AllianceTop))).all() == [] and (await db.scalars(select(AllianceKept))).all() == [], "the last team out takes the alliance and its lists"


# --- the calls ---------------------------------------------------------------------------------------------------------

import json  # noqa: E402

from terraforma.accounts.service import create_account, set_handle  # noqa: E402
from terraforma.game import Game  # noqa: E402
from terraforma.models import Map  # noqa: E402
from terraforma.testing import in_app_db  # noqa: E402

from .helpers import expect  # noqa: E402
from .test_fight_store import SEED  # noqa: E402

PASSWORD = "correct horse battery"


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def login(client, username):
    return {"X-CSRF-Token": expect(client.post("/api/login", json={"username": username, "password": PASSWORD}), 200).json()["csrf_token"]}


def call(client, who, method, url, body=None, status=200):
    headers = login(client, who)
    response = client.request(method, url, json=body, headers=headers) if body is not None else client.request(method, url, headers=headers)
    return expect(response, status).json()


@pytest.fixture
def pact(app_client):
    """Mike's Vanguard in the Iron Pact with Zed's Strangers (a member with the lowest role); Yan has no team in it.
    ``fights`` makes finished fights for the teams."""
    for name in ("Mike", "Zed", "Yan"):
        in_app_db(app_client, lambda db, name=name: create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True))
    ids = {}
    for who, name in (("Mike", "Vanguard"), ("Zed", "Strangers"), ("Yan", "Loners")):
        ids[name] = call(app_client, who, "POST", "/api/teams", {"name": name, "heroes": [{"name": f"{name} hero", "job": "fighter"}]}, 201)["id"]
    ids["alliance"] = call(app_client, "Mike", "POST", "/api/alliances", {"team_id": ids["Vanguard"], "name": "Iron Pact"}, 201)["id"]
    call(app_client, "Mike", "POST", f"/api/alliances/{ids['alliance']}/invite", {"team_id": ids["Vanguard"], "target_team_id": ids["Strangers"]})
    call(app_client, "Zed", "POST", f"/api/teams/{ids['Strangers']}/invitations/accept", {"alliance_id": ids["alliance"]})

    def fights(team, count=1, **given):
        async def make(db):
            hub = await db.scalar(select(Map.id).where(Map.name == "hub"))
            made = []
            for _ in range(count):
                record = FightRecord(guid=uuid.uuid4().hex, initial_state={}, map_id=hub, finished=True)
                db.add(record)
                await db.flush()
                db.add(FightTeam(fight_id=record.id, team_id=ids[team], party=0, pvp=False, won=True, overkill=given.get("overkill", 0.0), underdog=given.get("underdog", 0.0)))
                made.append(record.id)
            return made

        return in_app_db(app_client, make)

    app_client.fights = fights
    return app_client, ids


def test_the_calls_need_a_login_and_the_changing_ones_a_csrf_token(pact):
    client, ids = pact
    client.cookies.clear()
    for url in (f"/api/teams/{ids['Vanguard']}/fight-tags", f"/api/alliances/{ids['alliance']}/fight-tags"):
        assert client.get(url).status_code == 401
    login(client, "Mike")
    for url, body in ((f"/api/teams/{ids['Vanguard']}/kept", {"fight_id": 1}), (f"/api/teams/{ids['Vanguard']}/kept/remove", {"fight_id": 1}),
                      (f"/api/alliances/{ids['alliance']}/kept", {"team_id": ids["Vanguard"], "fight_id": 1}), (f"/api/alliances/{ids['alliance']}/kept/remove", {"fight_id": 1})):
        assert client.post(url, json=body).status_code == 403, url


def test_a_team_owner_sees_the_tags_and_kept_fights_of_their_own_team_only(pact):
    client, ids = pact
    view = call(client, "Mike", "GET", f"/api/teams/{ids['Vanguard']}/fight-tags")
    assert view == {"tags": {kind: None for kind in scoring.KINDS}, "kept": [], "slots": 10}
    call(client, "Mike", "GET", f"/api/teams/{ids['Strangers']}/fight-tags", status=404)
    call(client, "Mike", "GET", "/api/teams/9999/fight-tags", status=404)


def test_a_team_keeps_and_lets_go_of_its_fights_through_the_calls_up_to_its_slots(pact):
    client, ids = pact
    mine = client.fights("Vanguard", 11)
    theirs = client.fights("Strangers")[0]
    url = f"/api/teams/{ids['Vanguard']}/kept"
    headers = login(client, "Mike")

    def post(path, body, status=200):
        return expect(client.post(path, json=body, headers=headers), status).json()

    for fight in mine[:10]:
        view = post(url, {"fight_id": fight})
    assert len(view["kept"]) == 10
    post(url, {"fight_id": mine[10]}, 422)
    post(url, {"fight_id": theirs}, 404)
    post(url, {"fight_id": 0}, 422)
    assert len(post(f"{url}/remove", {"fight_id": mine[0]})["kept"]) == 9
    assert len(post(url, {"fight_id": mine[10]})["kept"]) == 10
    zed = login(client, "Zed")
    expect(client.post(url, json={"fight_id": mine[0]}, headers=zed), 404)


def test_the_alliance_view_is_for_its_members_and_its_slots_are_kept_by_a_team_whose_role_may_speak(pact):
    client, ids = pact
    url = f"/api/alliances/{ids['alliance']}"
    assert call(client, "Mike", "GET", f"{url}/fight-tags") == {"top": {kind: [] for kind in scoring.KINDS}, "kept": [], "top_size": 10, "slots": 50}
    call(client, "Zed", "GET", f"{url}/fight-tags")
    call(client, "Yan", "GET", f"{url}/fight-tags", status=404)
    fight = client.fights("Vanguard")[0]
    answer = call(client, "Mike", "POST", f"{url}/kept", {"team_id": ids["Vanguard"], "fight_id": fight})
    assert answer["kept"] == [{"fight_id": fight, "team_id": ids["Vanguard"]}]
    call(client, "Mike", "POST", f"{url}/kept", {"team_id": ids["Strangers"], "fight_id": fight}, 404)  # not his team
    call(client, "Mike", "POST", f"{url}/kept", {"team_id": ids["Vanguard"], "fight_id": fight + 99}, 404)
    call(client, "Yan", "POST", f"{url}/kept", {"team_id": ids["Loners"], "fight_id": fight}, 404)
    assert call(client, "Mike", "POST", f"{url}/kept/remove", {"fight_id": fight})["kept"] == []


def test_a_call_of_the_wrong_shape_is_refused(pact):
    client, ids = pact
    headers = login(client, "Mike")
    url = f"/api/teams/{ids['Vanguard']}/kept"
    assert client.post(url, json={"fight_id": "1"}, headers=headers).status_code == 422
    assert client.post(url, json={"fight_id": 1, "extra": 1}, headers=headers).status_code == 422
    assert client.post(f"/api/alliances/{ids['alliance']}/kept", json={"fight_id": 1}, headers=headers).status_code == 422
