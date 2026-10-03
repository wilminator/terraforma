"""Parties: collections of whole teams, formed, merged and left, with the game's size rule, on every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.content.models import Job
from terraforma.fights.build import party_side
from terraforma.fights.fight import build_fight
from terraforma.fights.rules import Rules
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.heroes.models import Hero, TeamMember
from terraforma.parties import service as parties
from terraforma.parties.models import Party, PartyTeam
from terraforma.testing import in_app_db
from terraforma.world.start import ensure_start

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20, "MP": 2, "Speed": 5, "Strength": 5}}]}


async def account(db, name="Mike"):
    if await db.scalar(select(func.count()).select_from(Job)) == 0:
        await load_content(db, SEED)  # (this expires what the session holds, so it is done before the first account)
    return await create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True)


async def team_of(db, owner, name, hero_names):
    team = await service.create_team(db, owner, name)
    for hero_name in hero_names:
        hero = await service.create_hero(db, owner, hero_name, "fighter")
        await service.add_to_team(db, owner, team.id, hero.id)
    return team


async def count(db, table):
    return await db.scalar(select(func.count()).select_from(table))


# --- forming and joining ---------------------------------------------------------------------------------------

async def test_a_party_of_one_team_stands_where_its_first_hero_stands(db):
    mike = await account(db)
    team = await team_of(db, mike, "Vanguard", ["Aria", "Bram"])
    party = await parties.create_party(db, team.id, 20)
    hub = await ensure_start(db)
    assert (party.map_id, party.x, party.y) == (hub.id, 0, 0)
    assert await parties.team_ids(db, party.id) == [team.id] and await parties.size(db, party.id) == 2
    assert (await parties.party_of(db, team.id)).id == party.id


async def test_a_team_with_no_heroes_can_still_start_a_party_on_the_hub(db):
    mike = await account(db)
    empty = await service.create_team(db, mike, "Empty")
    party = await parties.create_party(db, empty.id, 20)
    assert party.map_id == (await ensure_start(db)).id and await parties.size(db, party.id) == 0


async def test_a_team_is_in_at_most_one_party(db):
    mike = await account(db)
    one, two = await team_of(db, mike, "One", ["Aria"]), await team_of(db, mike, "Two", ["Bram"])
    first = await parties.create_party(db, one.id, 20)
    second = await parties.create_party(db, two.id, 20)
    for attempt in (parties.create_party(db, one.id, 20), parties.join_party(db, second.id, one.id, 20)):
        with pytest.raises(parties.PartyError, match="already in a party"):
            await attempt
    assert await parties.team_ids(db, first.id) == [one.id] and await parties.team_ids(db, second.id) == [two.id]


async def test_teams_from_different_accounts_can_share_a_party_in_the_order_they_joined(db):
    mike = await account(db)
    zed = await account(db, "Zed")
    first, second, third = await team_of(db, mike, "Team A", ["Aria", "Bram"]), await team_of(db, zed, "Team B", ["Cato"]), await team_of(db, mike, "Team C", ["Dara"])
    party = await parties.create_party(db, second.id, 20)
    await parties.join_party(db, party.id, first.id, 20)
    await parties.join_party(db, party.id, third.id, 20)
    assert await parties.team_ids(db, party.id) == [second.id, first.id, third.id]
    names = [await db.scalar(select(Hero.name).where(Hero.id == hero_id)) for hero_id in await parties.hero_ids(db, party.id)]
    assert names == ["Cato", "Aria", "Bram", "Dara"], "team by team, each by slot"


async def test_a_team_joins_only_if_there_is_a_place_for_every_hero(db):
    mike = await account(db)
    big, other, small = await team_of(db, mike, "Big", ["Aa1", "Aa2", "Aa3", "Aa4"]), await team_of(db, mike, "Other", ["Bb1", "Bb2", "Bb3", "Bb4"]), await team_of(db, mike, "Small", ["Cc1", "Cc2"])
    party = await parties.create_party(db, big.id, 6)
    with pytest.raises(parties.PartyError, match="room for 6 heroes: it has 4 and that team has 4"):
        await parties.join_party(db, party.id, other.id, 6)
    assert await parties.party_of(db, other.id) is None, "never joined in part"
    await parties.join_party(db, party.id, small.id, 6)
    assert await parties.size(db, party.id) == 6


async def test_a_team_too_big_for_any_party_cannot_start_one(db):
    mike = await account(db)
    team = await team_of(db, mike, "Big", ["Aa1", "Aa2", "Aa3", "Aa4"])
    with pytest.raises(parties.PartyError, match="room for 3 heroes, and that team has 4"):
        await parties.create_party(db, team.id, 3)
    assert await count(db, Party) == 0


async def test_unknown_teams_and_parties_are_not_found(db):
    await account(db)
    with pytest.raises(parties.NotFound):
        await parties.create_party(db, 999, 20)
    with pytest.raises(parties.NotFound):
        await parties.get_party(db, 999)


# --- leaving ----------------------------------------------------------------------------------------------------------------

async def test_a_whole_team_leaves_and_the_last_one_out_takes_the_party_with_it(db):
    mike = await account(db)
    one, two = await team_of(db, mike, "One", ["Aria"]), await team_of(db, mike, "Two", ["Bram"])
    party = await parties.create_party(db, one.id, 20)
    await parties.join_party(db, party.id, two.id, 20)
    assert await parties.leave_party(db, one.id) == party.id
    assert await parties.team_ids(db, party.id) == [two.id] and await parties.party_of(db, one.id) is None
    assert await db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == one.id)) == 1, "the team itself is whole"
    assert await parties.leave_party(db, two.id) == party.id
    assert await count(db, Party) == 0 and await count(db, PartyTeam) == 0
    assert await parties.leave_party(db, two.id) is None, "not in one: nothing to leave"


async def test_deleting_a_team_takes_it_out_of_its_party(db):
    mike = await account(db)
    one, two = await team_of(db, mike, "One", ["Aria"]), await team_of(db, mike, "Two", ["Bram"])
    party = await parties.create_party(db, one.id, 20)
    await parties.join_party(db, party.id, two.id, 20)
    await service.delete_team(db, mike, one.id)
    assert await parties.team_ids(db, party.id) == [two.id]
    await service.delete_team(db, mike, two.id)
    assert await count(db, Party) == 0


# --- merging ---------------------------------------------------------------------------------------------------------------------

async def test_parties_merge_whole_and_the_second_is_gone(db):
    mike = await account(db)
    a, b, c = await team_of(db, mike, "Team A", ["Aa1"]), await team_of(db, mike, "Team B", ["Bb1"]), await team_of(db, mike, "Team C", ["Cc1"])
    keep, absorb = await parties.create_party(db, a.id, 20), await parties.create_party(db, b.id, 20)
    await parties.join_party(db, absorb.id, c.id, 20)
    merged = await parties.merge_parties(db, keep.id, absorb.id, 20)
    assert merged.id == keep.id and await parties.team_ids(db, keep.id) == [a.id, b.id, c.id]
    assert await db.get(Party, absorb.id) is None and await parties.size(db, keep.id) == 3


async def test_parties_that_do_not_fit_together_do_not_merge_at_all(db):
    mike = await account(db)
    a, b = await team_of(db, mike, "Team A", ["Aa1", "Aa2", "Aa3"]), await team_of(db, mike, "Team B", ["Bb1", "Bb2", "Bb3"])
    first, second = await parties.create_party(db, a.id, 5), await parties.create_party(db, b.id, 5)
    with pytest.raises(parties.PartyError, match="room for 5 heroes: they have 3 and 3"):
        await parties.merge_parties(db, first.id, second.id, 5)
    assert await parties.team_ids(db, first.id) == [a.id] and await parties.team_ids(db, second.id) == [b.id]
    with pytest.raises(parties.PartyError, match="itself"):
        await parties.merge_parties(db, first.id, first.id, 5)


# --- a hero joining a team that is in a party ----------------------------------------------------------------------------------

async def test_a_hero_added_to_a_team_in_a_full_party_is_refused_and_the_game_sets_the_size(db):
    mike = await account(db)
    team = await team_of(db, mike, "Team A", ["Aa1", "Aa2"])
    await parties.create_party(db, team.id, 3)
    extra, more = await service.create_hero(db, mike, "Aa3", "fighter"), await service.create_hero(db, mike, "Aa4", "fighter")
    await service.add_to_team(db, mike, team.id, extra.id, 3)
    with pytest.raises(service.HeroError, match="party has room for 3 heroes and is full"):
        await service.add_to_team(db, mike, team.id, more.id, 3)
    assert await db.scalar(select(func.count()).select_from(TeamMember).where(TeamMember.team_id == team.id)) == 3
    await service.add_to_team(db, mike, team.id, more.id, 4)


class Tiny(Rules):
    party_size = 2


@pytest.fixture
def game(tmp_path):
    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    (seed_dir / "jobs.json").write_text(json.dumps(SEED["jobs"]))
    return Game(name="Tiny", seed_dir=seed_dir, rules=Tiny())


@pytest.mark.anyio(False)
def test_the_add_hero_call_follows_the_games_party_size(app_client):
    client = app_client
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    team = client.post("/api/teams", json={"name": "Team A"}, headers=headers).json()["id"]
    heroes = [client.post("/api/heroes", json={"name": name, "job": "fighter"}, headers=headers).json()["id"] for name in ("Aria", "Bram", "Cato")]
    for hero in heroes[:2]:
        assert client.post(f"/api/teams/{team}/add-hero", json={"hero_id": hero}, headers=headers).status_code == 200
    in_app_db(client, lambda db: parties.create_party(db, team, 2))
    refused = client.post(f"/api/teams/{team}/add-hero", json={"hero_id": heroes[2]}, headers=headers)
    assert refused.status_code >= 400 and "party" in refused.text


# --- entering the game ------------------------------------------------------------------------------------------------------------

async def test_a_team_that_plays_is_in_a_party_of_just_that_team_and_playing_again_changes_nothing(db):
    mike = await account(db)
    team = await team_of(db, mike, "Vanguard", ["Aria", "Bram"])
    assert await parties.party_of(db, team.id) is None
    party = await parties.play(db, team.id, 20)
    assert await parties.team_ids(db, party.id) == [team.id] and await parties.leader_account(db, party.id) == mike.id
    again = await parties.play(db, team.id, 20)
    assert again.id == party.id and await count(db, Party) == 1


async def test_a_team_with_no_heroes_cannot_play_and_a_team_already_in_a_bigger_party_keeps_it(db):
    mike = await account(db)
    empty = await service.create_team(db, mike, "Empty")
    with pytest.raises(parties.PartyError, match="needs a hero"):
        await parties.play(db, empty.id, 20)
    a, b = await team_of(db, mike, "Team A", ["Aa1"]), await team_of(db, mike, "Team B", ["Bb1"])
    party = await parties.create_party(db, a.id, 20)
    await parties.join_party(db, party.id, b.id, 20)
    assert (await parties.play(db, b.id, 20)).id == party.id and await parties.team_ids(db, party.id) == [a.id, b.id]


@pytest.mark.anyio(False)
def test_the_play_call_makes_the_party_and_is_for_the_teams_owner_only(app_client):
    client = app_client
    for name in ("Mike", "Zed"):
        in_app_db(client, lambda db, name=name: create_account(db, name, PASSWORD, email=f"{name.lower()}@example.com", confirmed=True))
    login = lambda name: {"X-CSRF-Token": client.post("/api/login", json={"username": name, "password": PASSWORD}).json()["csrf_token"]}  # noqa: E731
    mike = login("Mike")
    team = client.post("/api/teams", json={"name": "Vanguard"}, headers=mike).json()["id"]
    empty = client.post("/api/teams", json={"name": "Empty"}, headers=mike).json()["id"]
    hero = client.post("/api/heroes", json={"name": "Aria", "job": "fighter"}, headers=mike).json()["id"]
    assert client.post(f"/api/teams/{team}/add-hero", json={"hero_id": hero}, headers=mike).status_code == 200
    assert client.post(f"/api/teams/{empty}/play", json={}, headers=mike).status_code == 409, "no heroes"
    assert client.post(f"/api/teams/{team}/play", json={}).status_code == 403, "no CSRF token"
    played = client.post(f"/api/teams/{team}/play", json={}, headers=mike)
    assert played.status_code == 200 and played.json()["teams"] == [team] and set(played.json()) == {"party", "teams", "map_id", "x", "y"}
    assert client.post(f"/api/teams/{team}/play", json={}, headers=mike).json() == played.json(), "safe to repeat"
    zed = login("Zed")
    assert client.post(f"/api/teams/{team}/play", json={}, headers=zed).status_code == 404, "not Zed's team"


# --- a party as one side of a fight -----------------------------------------------------------------------------------------------

async def test_a_party_becomes_one_side_of_a_fight_in_groups(db):
    mike = await account(db)
    a, b = await team_of(db, mike, "Team A", ["Aa1", "Aa2", "Aa3", "Aa4"]), await team_of(db, mike, "Team B", ["Bb1", "Bb2", "Bb3"])
    party = await parties.create_party(db, a.id, 20)
    await parties.join_party(db, party.id, b.id, 20)

    class Threes(Rules):
        group_size = 3

    side = await party_side(db, party.id, Threes())
    assert [[fighter.name for fighter in group] for group in side.values()] == [["Aa1", "Aa2", "Aa3"], ["Aa4", "Bb1", "Bb2"], ["Bb3"]]
    assert all(fighter.charid is not None for group in side.values() for fighter in group)
    fight = build_fight({0: side, 1: {0: []}})
    assert len(fight.addresses()) == 7 and fight.get((0, 1, 2)).name == "Bb2"


async def test_the_default_party_is_twenty_heroes_in_four_groups_of_five(db):
    assert (Rules.party_size, Rules.group_size) == (20, 5)
    mike = await account(db)
    teams = [await team_of(db, mike, f"T{n}", [f"H{n}{m}" for m in range(4)]) for n in range(3)]
    party = await parties.create_party(db, teams[0].id, 20)
    for team in teams[1:]:
        await parties.join_party(db, party.id, team.id, 20)
    side = await party_side(db, party.id, Rules())
    assert [len(group) for group in side.values()] == [5, 5, 2]


# --- the leader ------------------------------------------------------------------------------------------------

async def two_players(db):
    """Mike's team Vanguard and Zed's team Rivals, one hero each."""
    mike = await account(db, "Mike")
    zed = await account(db, "Zed")
    mine = await team_of(db, mike, "Vanguard", ["Aria"])
    theirs = await team_of(db, zed, "Rivals", ["Zane"])
    return mike, zed, mine, theirs


async def hero_named(db, name):
    return await db.scalar(select(Hero).where(Hero.name == name))


async def test_a_new_party_is_led_by_the_player_who_founded_it(db):
    mike, zed, mine, theirs = await two_players(db)
    party = await parties.create_party(db, mine.id, 20)
    assert party.leader_account_id == mike.id and await parties.leader_account(db, party.id) == mike.id
    assert await parties.is_leader(db, await hero_named(db, "Aria"))


async def test_whoever_accepts_another_party_into_theirs_leads_it(db):
    mike, zed, mine, theirs = await two_players(db)
    first, second = await parties.create_party(db, mine.id, 20), await parties.create_party(db, theirs.id, 20)
    assert await parties.leader_account(db, second.id) == zed.id
    await parties.merge_parties(db, second.id, first.id, 20, accepted_by=zed.id)  # Zed accepts Mike's party into his
    assert await parties.leader_account(db, second.id) == zed.id
    assert not await parties.is_leader(db, await hero_named(db, "Aria")) and await parties.is_leader(db, await hero_named(db, "Zane"))


async def test_accepting_a_team_makes_the_accepting_player_the_leader_and_a_stranger_cannot_accept(db):
    mike, zed, mine, theirs = await two_players(db)
    party = await parties.create_party(db, mine.id, 20)
    with pytest.raises(parties.PartyError, match="only a player with a team"):
        await parties.join_party(db, party.id, theirs.id, 20, accepted_by=zed.id)
    assert await parties.party_of(db, theirs.id) is None, "nothing changed"
    await parties.join_party(db, party.id, theirs.id, 20)
    assert await parties.leader_account(db, party.id) == mike.id, "joining without being accepted by anyone keeps the leader"
    elsewhere = await team_of(db, zed, "Second", ["Zoe"])
    await parties.join_party(db, party.id, elsewhere.id, 20, accepted_by=zed.id)
    assert await parties.leader_account(db, party.id) == zed.id


async def test_when_the_leaders_last_team_leaves_the_owner_of_the_first_team_leads(db):
    mike, zed, mine, theirs = await two_players(db)
    party = await parties.create_party(db, mine.id, 20)
    await parties.join_party(db, party.id, theirs.id, 20)
    await parties.leave_party(db, mine.id)
    assert (await db.get(Party, party.id)).leader_account_id == zed.id
    assert await parties.leader_account(db, party.id) == zed.id


async def test_a_team_in_no_party_is_led_by_its_owner_and_a_hero_without_a_team_leads_nothing(db):
    mike, zed, mine, theirs = await two_players(db)
    assert await parties.is_leader(db, await hero_named(db, "Aria"))
    loose = await service.create_hero(db, mike, "Loose", "fighter")
    assert not await parties.is_leader(db, loose)
