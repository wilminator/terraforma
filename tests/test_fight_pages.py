"""Fight pages: a team's history, the fights a player can view, and the public showcase and coliseum pages. On every database."""

import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from terraforma import wallclock
from terraforma.accounts.service import create_account, set_handle
from terraforma.fightpages import service
from terraforma.fightpages.models import PublicFight
from terraforma.fights import live
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.fighttags import scoring
from terraforma.fighttags import service as tags
from terraforma.fighttags.models import AllianceKept, AllianceTop, FightTeam, TeamKept, TeamTag
from terraforma.game import Game
from terraforma.heroes import service as heroes
from terraforma.housekeeping import old_unkept_fights
from terraforma.profiles.models import TeamProfile
from terraforma.settings import Settings
from terraforma.social.models import Follow, ProfileBan
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_store import SEED
from .test_live_fights import RULES, a_team

pytestmark = pytest.mark.social

PASSWORD = "correct horse battery"


# --- helpers ----------------------------------------------------------------------------------------------------------

async def a_second_team(db, name="Zed"):
    """Another player's team (with one hero)."""
    account = await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)
    hero = await heroes.create_hero(db, account, f"{name} hero", "fighter")
    team = await heroes.create_team(db, account, f"{name} team")
    await heroes.add_to_team(db, account, team.id, hero.id)
    return hero, account, team


async def a_fight(db, hero, team, *, finished=True, won=True, pvp=False, overkill=0.0, underdog=0.0, age_days=0):
    """A fight with $hero in it, scored for $team."""
    record = FightRecord(guid=uuid.uuid4().hex, initial_state={}, map_id=hero.map_id, finished=finished)
    db.add(record)
    await db.flush()
    db.add(FightParticipant(fight_id=record.id, party=0, group_index=0, character=0, name=hero.name, hero_id=hero.id))
    if finished:
        db.add(FightTeam(fight_id=record.id, team_id=team.id, party=0, pvp=pvp, won=won, overkill=overkill, underdog=underdog))
    if age_days:
        record.updated_at = wallclock.now() - timedelta(days=age_days)
    await db.flush()
    return record


async def real_fight(db, team, rules=RULES, **marks):
    """A fight that really was played to a start (so it can be viewed and replayed), marked finished at once."""
    record = await live.start_team_fight(db, team, ["rat"], rules)
    record.finished = True
    await db.flush()
    return record


def ids(listing):
    return [entry["id"] for entry in listing]


# --- a team's history ---------------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_teams_history_is_its_finished_fights_newest_first_with_how_each_went(db):
    hero, _mike, team = await a_team(db)
    first = await a_fight(db, hero, team, overkill=3.0)
    second = await a_fight(db, hero, team, won=False)
    third = await a_fight(db, hero, team, pvp=True, underdog=2.0)
    running = await a_fight(db, hero, team, finished=False)
    history = await service.team_history(db, team.id)
    assert ids(history) == [third.id, second.id, first.id], "a running fight is not history yet"
    assert running.id not in ids(history)
    assert [(entry["pvp"], entry["won"]) for entry in history] == [(True, True), (False, False), (False, True)]
    assert history[0]["guid"] == third.guid and history[0]["at"].endswith("+00:00")


@pytest.mark.anyio
async def test_the_history_shows_what_the_team_keeps_and_the_tags_a_fight_holds_and_pages_back(db):
    hero, _mike, team = await a_team(db)
    old, middle, new = [await a_fight(db, hero, team) for _ in range(3)]
    db.add(TeamKept(team_id=team.id, fight_id=old.id))
    db.add(TeamTag(team_id=team.id, kind=scoring.PVE_UNDERDOG, fight_id=new.id, score=2.0))
    db.add(TeamTag(team_id=team.id, kind=scoring.PVE_OVERKILL, fight_id=new.id, score=4.0))
    await db.flush()
    history = {entry["id"]: entry for entry in await service.team_history(db, team.id)}
    assert (history[old.id]["kept"], history[middle.id]["kept"]) == (True, False)
    assert history[new.id]["tags"] == sorted([scoring.PVE_UNDERDOG, scoring.PVE_OVERKILL]) and history[old.id]["tags"] == []
    assert ids(await service.team_history(db, team.id, limit=2)) == [new.id, middle.id]
    assert ids(await service.team_history(db, team.id, before=middle.id)) == [old.id]


@pytest.mark.anyio
async def test_another_teams_fights_are_not_in_a_teams_history(db):
    hero, _mike, team = await a_team(db)
    zhero, _zed, zteam = await a_second_team(db)
    mine, theirs = await a_fight(db, hero, team), await a_fight(db, zhero, zteam)
    assert ids(await service.team_history(db, team.id)) == [mine.id]
    assert ids(await service.team_history(db, zteam.id)) == [theirs.id]


# --- the fights a player can view ---------------------------------------------------------------------------------------------

@pytest.mark.anyio
async def test_a_player_can_view_their_own_fights_running_and_finished_and_no_one_elses(db):
    hero, mike, team = await a_team(db)
    zhero, zed, zteam = await a_second_team(db)
    done, running = await a_fight(db, hero, team), await a_fight(db, hero, team, finished=False)
    theirs = await a_fight(db, zhero, zteam)
    seen = await service.viewable(db, mike.id)
    assert ids(seen) == [running.id, done.id]
    assert [entry["why"] for entry in seen] == [["mine"], ["mine"]] and seen[0]["over"] is False and seen[1]["over"] is True
    assert theirs.id not in ids(seen)
    assert await service.can_view(db, mike.id, done.id) and not await service.can_view(db, mike.id, theirs.id)
    assert ids(await service.viewable(db, zed.id)) == [theirs.id]


@pytest.mark.anyio
async def test_a_player_can_view_the_fights_of_the_players_they_follow_unless_they_were_barred(db):
    hero, mike, team = await a_team(db)
    zhero, zed, zteam = await a_second_team(db)
    yhero, yan, yteam = await a_second_team(db, "Yan")
    zeds, yans = await a_fight(db, zhero, zteam), await a_fight(db, yhero, yteam, finished=False)
    db.add(Follow(follower_id=mike.id, followed_id=zed.id))
    db.add(Follow(follower_id=mike.id, followed_id=yan.id))
    db.add(ProfileBan(owner_id=yan.id, banned_id=mike.id))
    await db.flush()
    seen = await service.viewable(db, mike.id)
    assert ids(seen) == [zeds.id] and seen[0]["why"] == ["following"]
    assert not await service.can_view(db, mike.id, yans.id), "a ban beats a follow, running or not"
    assert ids(await service.viewable(db, zed.id)) == [zeds.id], "following is one way: Zed sees only their own"
    await db.delete(await db.get(Follow, (mike.id, zed.id)))
    await db.flush()
    assert await service.viewable(db, mike.id) == []


@pytest.mark.anyio
async def test_a_fight_the_game_marked_public_is_viewable_by_everyone_signed_in(db):
    _hero, mike, _team = await a_team(db)
    zhero, _zed, zteam = await a_second_team(db)
    marked, plain = await a_fight(db, zhero, zteam), await a_fight(db, zhero, zteam)
    db.add(PublicFight(fight_id=marked.id))
    await db.flush()
    assert ids(await service.viewable(db, mike.id)) == [marked.id]
    assert (await service.viewable(db, mike.id))[0]["why"] == ["public"]
    assert not await service.can_view(db, mike.id, plain.id)


@pytest.mark.anyio
async def test_members_of_an_alliance_can_view_its_retained_and_top_fights(db):
    from terraforma.alliances import service as alliances
    from terraforma.alliances.hooks import Alliances

    hero, mike, team = await a_team(db)
    zhero, zed, zteam = await a_second_team(db)
    alliance = await alliances.found(db, Alliances(), team, "Iron Pact")
    kept, top, outside = await a_fight(db, zhero, zteam), await a_fight(db, zhero, zteam), await a_fight(db, zhero, zteam)
    db.add(AllianceKept(alliance_id=alliance.id, fight_id=kept.id, team_id=team.id))
    db.add(AllianceTop(alliance_id=alliance.id, kind=scoring.PVE_OVERKILL, fight_id=top.id, team_id=team.id, score=1.0))
    await db.flush()
    seen = await service.viewable(db, mike.id)
    assert ids(seen) == [top.id, kept.id] and all(entry["why"] == ["alliance"] for entry in seen)
    assert outside.id not in ids(seen) and ids(await service.viewable(db, zed.id)) == [outside.id, top.id, kept.id]


@pytest.mark.anyio
async def test_a_fight_viewable_for_several_reasons_is_listed_once_with_all_of_them_and_the_list_pages(db):
    hero, mike, team = await a_team(db)
    zhero, zed, zteam = await a_second_team(db)
    shared = await a_fight(db, hero, team)
    db.add(PublicFight(fight_id=shared.id))
    extras = [await a_fight(db, hero, team) for _ in range(3)]
    db.add(Follow(follower_id=mike.id, followed_id=zed.id))
    await db.flush()
    everything = await service.viewable(db, mike.id, limit=10)
    assert ids(everything) == [extras[2].id, extras[1].id, extras[0].id, shared.id]
    assert everything[-1]["why"] == ["mine", "public"]
    assert ids(await service.viewable(db, mike.id, limit=2)) == [extras[2].id, extras[1].id]
    assert ids(await service.viewable(db, mike.id, before=extras[1].id, limit=2)) == [extras[0].id, shared.id]


# --- public marks -------------------------------------------------------------------------------------------------------------

class Showy(Rules):
    def public_fight(self, fight):
        return True


@pytest.mark.anyio
async def test_a_fight_is_public_only_when_the_games_rules_say_so_and_a_public_fight_is_kept_for_good(db):
    hero, _mike, team = await a_team(db)
    assert Rules().public_fight(None) is False
    plain = await real_fight(db, team)
    shown = await real_fight(db, team)
    for record, rules in ((plain, RULES), (shown, Showy())):
        fight, _played = await live.store.load_state(db, record, RULES)
        fight.over = True
        await tags.record_fight(db, record, fight, rules, [])
        record.updated_at = wallclock.now() - timedelta(days=400)
    await db.flush()
    assert [row.fight_id for row in (await db.scalars(select(PublicFight))).all()] == [shown.id]
    removed = await old_unkept_fights(db, wallclock.timestamp(), Settings(session_secret="x" * 32), Rules())
    assert removed == 1 and await db.get(FightRecord, shown.id) is not None and await db.get(FightRecord, plain.id) is None


# --- the public pages -------------------------------------------------------------------------------------------------------------

async def showing(db, team, fight, kind=scoring.PVE_UNDERDOG, score=2.0, visible=True):
    db.add(TeamTag(team_id=team.id, kind=kind, fight_id=fight.id, score=score))
    db.add(TeamProfile(team_id=team.id, token=uuid.uuid4().hex[:22], visible=visible))
    await db.flush()


@pytest.mark.anyio
async def test_the_showcase_shows_only_the_best_fights_of_teams_their_player_made_visible(db):
    hero, _mike, team = await a_team(db)
    zhero, _zed, zteam = await a_second_team(db)
    visible, hidden = await real_fight(db, team), await a_fight(db, zhero, zteam)
    with pytest.raises(service.NotFound):
        await service.showcase(db, RULES)
    await showing(db, team, visible)
    await showing(db, zteam, hidden, visible=False)
    for _ in range(3):
        shown = await service.showcase(db, RULES)
        assert shown["guid"] == visible.guid and shown["kind"] == scoring.PVE_UNDERDOG and shown["score"] == 2.0
    with pytest.raises(service.NotFound):
        await service.showcase(db, RULES, scoring.PVP_OVERKILL)


@pytest.mark.anyio
async def test_the_showcase_picks_among_the_candidates_by_the_worlds_stream_and_changes_with_the_clock(db, later):
    hero, _mike, team = await a_team(db)
    fights = [await real_fight(db, team) for _ in range(4)]
    for fight, kind in zip(fights, scoring.KINDS):
        db.add(TeamTag(team_id=team.id, kind=kind, fight_id=fight.id, score=1.0))
    db.add(TeamProfile(team_id=team.id, token="t" * 22, visible=True))
    await db.flush()
    later(0)
    again = [(await service.showcase(db, RULES))["guid"], (await service.showcase(db, RULES))["guid"]]
    assert again[0] == again[1], "the same second, the same pick"
    seen = set()
    for second in range(60):
        later(second)
        seen.add((await service.showcase(db, RULES))["guid"])
    assert seen == {fight.guid for fight in fights}, "over a minute every candidate comes up"


@pytest.mark.anyio
async def test_the_coliseum_lists_every_public_fight_newest_first_and_pages(db):
    hero, _mike, team = await a_team(db)
    plain = await a_fight(db, hero, team)
    public = [await a_fight(db, hero, team) for _ in range(3)]
    for record in public:
        db.add(PublicFight(fight_id=record.id))
    await db.flush()
    listing = await service.coliseum(db)
    assert ids(listing) == [public[2].id, public[1].id, public[0].id] and plain.id not in ids(listing)
    assert ids(await service.coliseum(db, limit=2)) == [public[2].id, public[1].id]
    assert ids(await service.coliseum(db, before=public[1].id)) == [public[0].id]


@pytest.mark.anyio
async def test_only_a_finished_fight_that_is_public_or_a_visible_teams_best_can_be_watched_by_its_name(db):
    hero, _mike, team = await a_team(db)
    zhero, _zed, zteam = await a_second_team(db)
    record = await real_fight(db, team)
    hidden = await a_fight(db, zhero, zteam)
    await showing(db, zteam, hidden, visible=False)
    for guid in (record.guid, hidden.guid, "0" * 32):
        with pytest.raises(service.NotFound):
            await service.public_replay(db, RULES, guid)
    db.add(PublicFight(fight_id=record.id))
    await db.flush()
    replay = await service.public_replay(db, RULES, record.guid)
    assert replay["fight"]["guid"] == record.guid and replay["rounds"] == []
    running = await live.start_team_fight(db, team, ["rat"], RULES)
    db.add(PublicFight(fight_id=running.id))
    await db.flush()
    with pytest.raises(service.NotFound):
        await service.public_replay(db, RULES, running.guid)


@pytest.mark.anyio
async def test_a_replay_has_every_round_played_with_its_events(db):
    from terraforma.fights.combatant import Command
    from .test_live_fights import FOE, HERO

    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], RULES)
    await live.submit_command(db, record, mike.id, HERO, Command.ATTACK_RIGHT, 0, FOE, RULES)
    await live.resolve_round(db, record, RULES)
    replay = await service.viewable_replay(db, RULES, mike.id, record.id)
    assert [each["round"] for each in replay["rounds"]] == [1] and replay["rounds"][0]["events"]
    assert replay["fight"]["round"] == 2
    assert replay["joins"] == {}
    with pytest.raises(service.NotFound):
        await service.viewable_replay(db, RULES, mike.id + 1000, record.id)


@pytest.mark.anyio
async def test_a_replay_starts_from_the_fight_as_it_began(db):
    """Playing the rounds back needs the state before round 1, not the one the fight ended in (and no deadline: nothing waits)."""
    from terraforma.fights.combatant import Command
    from .test_live_fights import FOE, HERO

    class Landing(Rules):
        """Every attack lands: a world's seed is random per test, so with real dice the one blow could miss and leave the fighters as they were."""

        def hit_chance(self, accuracy, dodge):
            return 100.0

    rules = Landing()
    hero, mike, team = await a_team(db)
    record = await live.start_team_fight(db, team, ["rat"], rules)
    began = await live.view(db, record, rules)
    await live.submit_command(db, record, mike.id, HERO, Command.ATTACK_RIGHT, 0, FOE, rules)
    await live.resolve_round(db, record, rules)
    replay = await service.viewable_replay(db, rules, mike.id, record.id)
    assert replay["start"]["deadline"] is None and replay["start"] == {**began, "deadline": None} and replay["start"]["round"] == 1 and not replay["start"]["over"]
    assert replay["start"]["fighters"] != replay["fight"]["fighters"]  # (the blow landed: somebody's gauge fell)
    assert all(not each["yours"] and "menu" not in each for each in replay["start"]["fighters"])  # (a replay commands nobody)


# --- the calls ----------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def login(client, username):
    return {"X-CSRF-Token": expect(client.post("/api/login", json={"username": username, "password": PASSWORD}), 200).json()["csrf_token"]}


@pytest.fixture
def pages(app_client):
    """Mike and Zed, each with a team. ``fights`` plays out real finished fights for a team, marked as asked."""
    ids_ = {}
    for name in ("Mike", "Zed"):
        in_app_db(app_client, lambda db, name=name: create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True))
        body = {"name": f"{name} team", "heroes": [{"name": f"{name} hero", "job": "fighter"}]}
        ids_[name] = expect(app_client.post("/api/teams", json=body, headers=login(app_client, name)), 201).json()["id"]

    def fights(team_name, count=1, public=False, tag=None, visible=False):
        async def make(db):
            from terraforma.heroes.models import Team

            team = await db.get(Team, ids_[team_name])
            made = []
            for _ in range(count):
                record = await real_fight(db, team)
                db.add(FightTeam(fight_id=record.id, team_id=team.id, party=0, pvp=False, won=True))
                if public:
                    db.add(PublicFight(fight_id=record.id))
                if tag:
                    db.add(TeamTag(team_id=team.id, kind=tag, fight_id=record.id, score=1.0))
                    if await db.get(TeamProfile, team.id) is None:
                        db.add(TeamProfile(team_id=team.id, token=uuid.uuid4().hex[:22], visible=visible))
                made.append((record.id, record.guid))
            return made

        return in_app_db(app_client, make)

    app_client.fights = fights
    return app_client, ids_


def test_the_signed_in_calls_need_a_login_and_the_public_ones_do_not(pages):
    client, ids_ = pages
    client.cookies.clear()
    for url in (f"/api/teams/{ids_['Mike']}/fights", "/api/fight-pages/viewable", "/api/fight-pages/1/replay"):
        assert client.get(url).status_code == 401, url
    assert expect(client.get("/api/public/fights/coliseum"), 200).json() == []
    assert client.get("/api/public/fights/showcase").status_code == 404, "nothing to show yet"
    assert client.get("/api/public/fights/" + "0" * 32 + "/replay").status_code == 404


def test_a_team_owner_sees_the_history_of_their_own_team_only(pages):
    client, ids_ = pages
    made = client.fights("Mike", 2)
    login(client, "Mike")
    history = expect(client.get(f"/api/teams/{ids_['Mike']}/fights"), 200).json()
    assert [entry["id"] for entry in history] == [made[1][0], made[0][0]]
    assert [entry["id"] for entry in expect(client.get(f"/api/teams/{ids_['Mike']}/fights?limit=1&before={made[1][0]}"), 200).json()] == [made[0][0]]
    assert client.get(f"/api/teams/{ids_['Zed']}/fights").status_code == 404
    assert client.get("/api/teams/9999/fights").status_code == 404


def test_the_viewable_list_and_replay_follow_the_same_rules_over_the_calls(pages):
    client, _ids = pages
    public = client.fights("Zed", 1, public=True)[0]
    private = client.fights("Zed", 1)[0]
    mine = client.fights("Mike", 1)[0]
    login(client, "Mike")
    listing = expect(client.get("/api/fight-pages/viewable"), 200).json()
    assert [(entry["id"], entry["why"]) for entry in listing] == [(mine[0], ["mine"]), (public[0], ["public"])]
    replay = expect(client.get(f"/api/fight-pages/{public[0]}/replay"), 200).json()
    assert replay["fight"]["guid"] == public[1] and replay["rounds"] == []
    assert client.get(f"/api/fight-pages/{private[0]}/replay").status_code == 404
    assert client.get("/api/fight-pages/99999/replay").status_code == 404


def test_the_public_pages_show_the_coliseum_and_a_visible_teams_best_fight_to_anyone(pages):
    client, _ids = pages
    coliseum = client.fights("Zed", 2, public=True)
    best = client.fights("Mike", 1, tag=scoring.PVE_UNDERDOG, visible=True)[0]
    hidden = client.fights("Zed", 1, tag=scoring.PVE_OVERKILL, visible=False)[0]
    client.cookies.clear()
    listing = expect(client.get("/api/public/fights/coliseum"), 200).json()
    assert [entry["guid"] for entry in listing] == [coliseum[1][1], coliseum[0][1]]
    assert [entry["id"] for entry in expect(client.get(f"/api/public/fights/coliseum?limit=1&before={coliseum[1][0]}"), 200).json()] == [coliseum[0][0]]
    shown = expect(client.get("/api/public/fights/showcase"), 200).json()
    assert shown["guid"] == best[1] and shown["kind"] == scoring.PVE_UNDERDOG and shown["replay"]["fight"]["guid"] == best[1]
    assert client.get("/api/public/fights/showcase?kind=" + scoring.PVE_OVERKILL).status_code == 404, "that tag is on a hidden team"
    assert client.get(f"/api/public/fights/{hidden[1]}/replay").status_code == 404
    assert expect(client.get(f"/api/public/fights/{best[1]}/replay"), 200).json()["fight"]["guid"] == best[1]
    assert expect(client.get(f"/api/public/fights/{coliseum[0][1]}/replay"), 200).json()["fight"]["id"] == coliseum[0][0]
    assert "account" not in json.dumps(shown)


@pytest.mark.parametrize(
    "url",
    [
        "/api/public/fights/not-a-guid/replay", "/api/public/fights/coliseum?limit=0", "/api/public/fights/coliseum?limit=51",
        "/api/public/fights/coliseum?before=0", "/api/public/fights/coliseum?before=x", "/api/public/fights/showcase?kind=nonsense",
    ],
)
def test_a_call_of_the_wrong_shape_is_refused_before_any_game_code(pages, url):
    client, _ids = pages
    assert client.get(url).status_code == 422
