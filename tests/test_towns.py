"""Towns: a party comes apart into its teams and is put back together when they are ready to leave, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.fights import live
from terraforma.fights.models import FightParticipant, FightRecord
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.heroes.models import Hero, Team, TeamMember
from terraforma.models import Account, Map
from terraforma.parties import service as parties
from terraforma.parties.models import Party
from terraforma.testing import in_app_db
from terraforma.world.rng import WorldRng
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.towns.models import TownNotice, TownTeam, TownVisit
from terraforma.towns.service import TownError

from .helpers import expect

anyio = pytest.mark.anyio
pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20, "MP": 2, "Speed": 5, "Strength": 5}}]}
TOWNS = Towns()


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def party_of_three(db):
    """Mike's Vanguard (Aria) and Rearguard (Bram) and Zed's Scouts (Cleo), in one party in that order."""
    mike, zed = await account(db), await account(db, "Zed")
    teams = []
    for owner, name, hero in ((mike, "Vanguard", "Aria"), (mike, "Rearguard", "Bram"), (zed, "Scouts", "Cleo")):
        team = await service.create_team(db, owner, name)
        await service.add_to_team(db, owner, team.id, (await service.create_hero(db, owner, hero, "fighter")).id)
        teams.append(team)
    party = await parties.create_party(db, teams[0].id, 20)
    await parties.join_party(db, party.id, teams[1].id, 20)
    await parties.join_party(db, party.id, teams[2].id, 20)
    return party, [team.id for team in teams]


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


# --- coming apart ----------------------------------------------------------------------------------------------------

async def test_a_party_that_comes_into_a_town_comes_apart_into_its_teams_by_default(db):
    party, teams = await party_of_three(db)
    visit = await towns_service.enter_town(db, TOWNS, party.id)
    assert visit.formation == teams and await towns_service.is_suspended(db, party.id)
    rows = (await db.scalars(select(TownTeam).order_by(TownTeam.group))).all()
    assert [(row.team_id, row.group, row.waiting) for row in rows] == [(teams[0], 0, False), (teams[1], 1, False), (teams[2], 2, False)]
    with pytest.raises(TownError):
        await towns_service.enter_town(db, TOWNS, party.id)


async def test_a_game_can_keep_a_players_own_teams_together_in_town(db):
    class OwnerGroups(Towns):
        async def groups(self, session, party_id, team_ids):
            return [[team_ids[0], team_ids[1]], [team_ids[2]]]

    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, OwnerGroups(), party.id)
    groups = {row.team_id: row.group for row in (await db.scalars(select(TownTeam))).all()}
    assert groups == {teams[0]: 0, teams[1]: 0, teams[2]: 1}


async def test_groups_that_lose_or_double_a_team_are_refused(db):
    class Bad(Towns):
        async def groups(self, session, party_id, team_ids):
            return [team_ids[:1]]

    party, _teams = await party_of_three(db)
    with pytest.raises(TownError):
        await towns_service.enter_town(db, Bad(), party.id)
    assert await count(db, TownVisit) == 0


async def test_a_party_in_a_town_cannot_be_changed_until_it_is_whole(db):
    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, TOWNS, party.id)
    other = await service.create_team(db, await account(db, "Ivy"), "Ivys")
    with pytest.raises(parties.PartyError):
        await parties.join_party(db, party.id, other.id, 20)
    lone = await parties.create_party(db, other.id, 20)
    with pytest.raises(parties.PartyError):
        await parties.merge_parties(db, lone.id, party.id, 20)
    with pytest.raises(parties.PartyError):
        await parties.leave_party(db, teams[0])


async def test_deleting_a_team_in_a_town_is_refused_as_a_hero_error_not_a_crash(db):
    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, TOWNS, party.id)
    team = await db.get(Team, teams[0])
    with pytest.raises(service.HeroError):
        await service.delete_team(db, await db.get(Account, team.account_id), team.id)
    assert await count(db, TownTeam) == 3


# --- leaving ---------------------------------------------------------------------------------------------------------

async def test_teams_wait_the_others_are_told_and_the_last_to_be_ready_puts_the_party_back_together(db):
    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, TOWNS, party.id)
    hub = await db.get(Map, party.map_id)
    # Everyone wanders off the party's spot, so being put back shows.
    await db.execute(Hero.__table__.update().values(x=7, y=9))
    assert await towns_service.ready(db, TOWNS, teams[0]) == "waiting"
    assert (await towns_service.view(db, teams[0]))["waiting"] is True
    told = (await towns_service.view(db, teams[1]))["ready"], (await towns_service.view(db, teams[2]))["ready"]
    assert told == ([teams[0]], [teams[0]]), "the teams that are not waiting are told"
    assert (await towns_service.view(db, teams[0]))["ready"] == [], "and the one that is ready is not told about itself"
    assert await towns_service.ready(db, TOWNS, teams[1]) == "waiting"
    assert (await towns_service.view(db, teams[2]))["ready"] == [teams[0], teams[1]]
    assert await towns_service.ready(db, TOWNS, teams[2]) == "reformed"
    assert not await towns_service.is_suspended(db, party.id)
    assert await count(db, TownTeam) == await count(db, TownNotice) == 0
    assert await parties.team_ids(db, party.id) == teams, "the formation it had"
    assert {(hero.map_id, hero.x, hero.y) for hero in (await db.scalars(select(Hero))).all()} == {(hub.id, party.x, party.y)}, "put back on the map as one unit"


async def test_a_waiting_group_goes_together_and_comes_back_together(db):
    class OwnerGroups(Towns):
        async def groups(self, session, party_id, team_ids):
            return [[team_ids[0], team_ids[1]], [team_ids[2]]]

    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, OwnerGroups(), party.id)
    await towns_service.ready(db, OwnerGroups(), teams[0])
    waiting = {row.team_id: row.waiting for row in (await db.scalars(select(TownTeam))).all()}
    assert waiting == {teams[0]: True, teams[1]: True, teams[2]: False}, "the whole group is ready"
    assert [(n.team_id, n.about_team_id) for n in (await db.scalars(select(TownNotice).order_by(TownNotice.about_team_id))).all()] == [(teams[2], teams[0]), (teams[2], teams[1])]
    await towns_service.come_back(db, teams[1])
    waiting = {row.team_id: row.waiting for row in (await db.scalars(select(TownTeam))).all()}
    assert waiting == {teams[0]: False, teams[1]: False, teams[2]: False}
    assert await count(db, TownNotice) == 0, "nobody is waiting on them any more"


async def test_a_game_can_say_a_team_may_not_start_waiting(db):
    class Busy(Towns):
        async def may_wait(self, session, team_id):
            return "that team is in a fight"

    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, Busy(), party.id)
    with pytest.raises(TownError, match="in a fight"):
        await towns_service.ready(db, Busy(), teams[0])
    assert (await towns_service.view(db, teams[0]))["waiting"] is False


async def test_a_team_that_leaves_the_party_in_town_is_not_waited_for(db):
    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, TOWNS, party.id)
    await towns_service.ready(db, TOWNS, teams[0])
    await towns_service.ready(db, TOWNS, teams[1])
    await towns_service.leave_party(db, teams[2])  # the one everyone was waiting for goes its own way
    assert not await towns_service.is_suspended(db, party.id), "the rest were all waiting, so the party is whole again"
    assert await parties.team_ids(db, party.id) == teams[:2]
    assert await parties.party_of(db, teams[2]) is None


async def test_the_last_team_to_leave_ends_the_visit_and_the_party(db):
    party, teams = await party_of_three(db)
    await towns_service.enter_town(db, TOWNS, party.id)
    for team in teams:
        await towns_service.leave_party(db, team)
    assert await count(db, TownVisit) == await count(db, TownTeam) == await count(db, Party) == 0


async def test_a_team_not_in_a_town_has_nothing_to_do_there(db):
    _party, teams = await party_of_three(db)
    assert await towns_service.view(db, teams[0]) == {"in_town": False}
    for call in (towns_service.ready, ):
        with pytest.raises(TownError):
            await call(db, TOWNS, teams[0])
    with pytest.raises(TownError):
        await towns_service.come_back(db, teams[0])
    with pytest.raises(TownError):
        await towns_service.leave_party(db, teams[0])


async def test_what_makes_a_place_a_town_is_the_games_rule(db):
    class Hub(Towns):
        async def is_town(self, session, map_id, x, y):
            return (x, y) == (0, 0)

    assert await Hub().is_town(db, 1, 0, 0) is True and await Hub().is_town(db, 1, 3, 4) is False


# --- the hub, and leaving town ---------------------------------------------------------------------------------------------

MONSTERS = {
    "personalities": [{"key": "plain", "name": "Plain"}],
    "monsters": [{"key": key, "name": key.title(), "personality": "plain", "xp_reward": 3, "gold_reward": 1, "stats": {"HP": hp, "Strength": 2, "Speed": 1}}
                 for key, hp in (("slime", 8), ("bat", 10), ("rat", 6))],
}


async def with_monsters(db):
    await load_content(db, {**SEED, **MONSTERS})


async def test_the_hub_is_a_town_by_default_and_a_party_formed_there_comes_apart(db):
    party, teams = await party_of_three(db)
    hub = await db.get(Party, party.id)
    assert await TOWNS.is_town(db, hub.map_id, hub.x, hub.y) is True
    assert await TOWNS.is_town(db, hub.map_id + 1000, 0, 0) is False
    visit = await towns_service.settle(db, TOWNS, party.id)
    assert visit is not None and await towns_service.is_suspended(db, party.id)
    assert await towns_service.settle(db, TOWNS, party.id) is None, "already apart"
    assert (await towns_service.view(db, teams[0]))["in_town"] is True


async def test_a_party_outside_a_town_is_not_suspended(db):
    class Nowhere(Towns):
        async def is_town(self, session, map_id, x, y):
            return False

    party, _teams = await party_of_three(db)
    assert await towns_service.settle(db, Nowhere(), party.id) is None


async def test_leave_town_runs_a_fight_once_everyone_is_together(db):
    await with_monsters(db)
    party, teams = await party_of_three(db)
    await towns_service.settle(db, TOWNS, party.id)
    rules = Rules()
    for team in teams[:2]:
        assert await towns_service.leave(db, TOWNS, rules, team) == ("waiting", None)
    assert await count(db, FightRecord) == 0, "not until the last team is ready"
    result, fight = await towns_service.leave(db, TOWNS, rules, teams[2])
    assert result == "reformed" and fight is not None
    assert await count(db, FightRecord) == 1
    heroes = set(await parties.hero_ids(db, party.id))
    in_fight = set((await db.scalars(select(FightParticipant.hero_id).where(FightParticipant.fight_id == fight.id, FightParticipant.hero_id.is_not(None)))).all())
    assert in_fight == heroes, "every hero of every team is in it"


async def test_a_team_in_a_running_fight_cannot_leave_town(db):
    await with_monsters(db)
    party, teams = await party_of_three(db)
    await towns_service.settle(db, TOWNS, party.id)
    hero = (await parties.hero_ids(db, party.id))[0]
    first_team = (await db.scalars(select(TeamMember.team_id).where(TeamMember.hero_id == hero))).first()
    await live.start_party_fight(db, party.id, ["slime"], Rules())
    with pytest.raises(TownError, match="in a fight"):
        await towns_service.ready(db, TOWNS, first_team)
    assert (await towns_service.view(db, first_team))["waiting"] is False


async def test_the_default_encounter_is_repeatable_and_stays_near_the_party_strength(db):
    await with_monsters(db)
    party, _teams = await party_of_three(db)
    rules, rng = Rules(), WorldRng(7)
    weak = await TOWNS.encounter(db, rules, party.id, 1, rng, 0)
    assert len(weak) == 1, "a party too weak for any still meets one monster"
    strong = await TOWNS.encounter(db, rules, party.id, 10**9, rng, 0)
    assert 1 < len(strong) <= rules.party_size and set(strong) <= {"slime", "bat", "rat"}
    assert strong == await TOWNS.encounter(db, rules, party.id, 10**9, rng, 0), "the same world gives the same monsters"


async def test_no_monsters_means_no_fight(db):
    party, teams = await party_of_three(db)
    await towns_service.settle(db, TOWNS, party.id)
    for team in teams[:2]:
        await towns_service.leave(db, TOWNS, Rules(), team)
    assert await towns_service.leave(db, TOWNS, Rules(), teams[2]) == ("reformed", None)


async def test_a_game_chooses_the_monsters(db):
    class Rats(Towns):
        async def encounter(self, session, rules, party_id, strength, rng, number):
            return ["rat", "rat"]

    await with_monsters(db)
    party, teams = await party_of_three(db)
    await towns_service.settle(db, Rats(), party.id)
    for team in teams[:2]:
        await towns_service.leave(db, Rats(), Rules(), team)
    _result, fight = await towns_service.leave(db, Rats(), Rules(), teams[2])
    names = (await db.scalars(select(FightParticipant.name).where(FightParticipant.fight_id == fight.id, FightParticipant.hero_id.is_(None)))).all()
    assert sorted(names) == ["Rat", "Rat"]


# --- the calls -------------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in SEED.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir)


def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    token = client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]
    return {"X-CSRF-Token": token}


def test_a_teams_owner_reads_and_acts_in_a_town_and_nobody_else_does(app_client):
    mike = sign_in(app_client, "Mike")
    teams = [expect(app_client.post("/api/teams", json={"name": name}, headers=mike), 201).json()["id"] for name in ("Vanguard", "Rearguard")]
    zed = sign_in(app_client, "Zed")  # (now logged in as Zed)

    async def form(db):
        party = await parties.create_party(db, teams[0], 20)
        await parties.join_party(db, party.id, teams[1], 20)
        await towns_service.enter_town(db, TOWNS, party.id)

    in_app_db(app_client, form)
    url = f"/api/teams/{teams[0]}/town"
    expect(app_client.get(url), 404)  # Zed's login, but not his team
    expect(app_client.post(f"{url}/ready", json={}, headers=zed), 404)
    mike = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    assert expect(app_client.get(url), 200).json()["teams"] == [{"team": teams[0], "group": 0, "waiting": False}, {"team": teams[1], "group": 1, "waiting": False}]
    answer = expect(app_client.post(f"{url}/ready", json={}, headers=mike), 200).json()
    assert answer["result"] == "waiting" and answer["waiting"] is True
    other = expect(app_client.get(f"/api/teams/{teams[1]}/town"), 200).json()
    assert other["ready"] == [teams[0]], "the other team has been told"
    assert expect(app_client.post(f"{url}/come-back", json={}, headers=mike), 200).json()["waiting"] is False
    expect(app_client.post(f"{url}/ready", json={}), 403)  # no CSRF token
    assert expect(app_client.post(f"{url}/leave-party", json={}, headers=mike), 200).json() == {"in_town": False}
    last = expect(app_client.post(f"/api/teams/{teams[1]}/town/ready", json={}, headers=mike), 200).json()
    assert last["result"] == "reformed" and last["fight"] is None, "this game has no monsters to meet"
    assert expect(app_client.get(f"/api/teams/{teams[1]}/town"), 200).json() == {"in_town": False}
