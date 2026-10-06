"""The adventurers guild: adding your own team to a party, asking an ally's open party, and answering the asks, on every database."""

import pytest
from sqlalchemy import func, select

from terraforma.accounts.service import create_account
from terraforma.alliances.models import Alliance, AllianceMember
from terraforma.content.loader import load_content
from terraforma.guild import service
from terraforma.guild.hooks import GuestPass, Guild
from terraforma.heroes import service as heroes
from terraforma.heroes.models import Hero
from terraforma.npcs import service as npcs
from terraforma.npcs.hooks import Npcs
from terraforma.reach.hooks import Reach
from terraforma.npcs.state import DialogState
from terraforma.parties import service as parties
from terraforma.parties.models import Party, PartyRequest
from terraforma.profiles.models import TeamProfile
from terraforma.standing import service as standing
from terraforma.standing.models import StandingStatus
from terraforma import testing
from terraforma.testing import in_app_db
from terraforma.towns import service as towns
from terraforma.towns.hooks import Towns

from .helpers import expect

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
SEED = {"jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 20}}], "statuses": [{"key": "guest", "name": "Guest", "kind": "good"}]}
SIZE = 20
NPCS, GUILD = Npcs(), Guild()
REACH = Reach()


class World:
    """Mike's Vanguard (Aria) is playing, in a party. His Rearguard (Bram) and Reserve (Cleo) are not; Zed's Scouts (Dax) play too."""


async def a_world(db):
    await load_content(db, SEED)
    w = World()
    w.mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    w.zed = await create_account(db, "Zed", PASSWORD, email="zed@example.com", confirmed=True)
    w.team, w.hero = {}, {}
    for owner, team_name, hero_name in ((w.mike, "Vanguard", "Aria"), (w.mike, "Rearguard", "Bram"), (w.mike, "Reserve", "Cleo"), (w.zed, "Scouts", "Dax")):
        team = await heroes.create_team(db, owner, team_name)
        hero = await heroes.create_hero(db, owner, hero_name, "fighter")
        await heroes.add_to_team(db, owner, team.id, hero.id)
        w.team[team_name], w.hero[team_name] = team.id, hero
    w.party = {name: (await parties.play(db, w.team[name], SIZE)).id for name in ("Vanguard", "Scouts")}
    return w


async def ally(db, *team_ids):
    alliance = Alliance(name="Guildmates", name_key="guildmates")
    db.add(alliance)
    await db.flush()
    for team_id in team_ids:
        db.add(AllianceMember(alliance_id=alliance.id, team_id=team_id, role="member"))
    await db.flush()


# --- adding your own team ----------------------------------------------------------------------------------------------

async def test_a_team_that_is_not_playing_joins_the_party_and_stands_with_the_hero(db):
    w = await a_world(db)
    w.hero["Vanguard"].x, w.hero["Vanguard"].y = 3, 4
    await db.flush()
    assert {team["name"] for team in await service.teams_to_add(db, w.hero["Vanguard"])} == {"Rearguard", "Reserve"}
    done = await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE)
    assert done == {"party": w.party["Vanguard"], "team": w.team["Rearguard"]}
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"], w.team["Rearguard"]]
    bram = await db.get(Hero, w.hero["Rearguard"].id)
    assert (bram.x, bram.y) == (3, 4)
    assert {team["name"] for team in await service.teams_to_add(db, w.hero["Vanguard"])} == {"Reserve"}


async def test_a_team_playing_alone_leaves_its_own_party_to_join(db):
    w = await a_world(db)
    await parties.play(db, w.team["Rearguard"], SIZE)
    before = await db.scalar(select(func.count()).select_from(Party))
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE)
    assert await db.scalar(select(func.count()).select_from(Party)) == before - 1, "its one-team party is gone"
    assert (await parties.party_of(db, w.team["Rearguard"])).id == w.party["Vanguard"]


async def test_the_guild_adds_only_your_own_teams_that_have_heroes_and_are_free_to_move(db):
    w = await a_world(db)
    empty = await heroes.create_team(db, w.mike, "Nobody")
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE)
    await parties.join_party(db, w.party["Vanguard"], w.team["Reserve"], SIZE)  # in a party with others now
    for team_id, why in ((w.team["Scouts"], "someone else's"), (w.team["Vanguard"], "already in it"), (empty.id, "no heroes"), (w.team["Reserve"], "in a party of others")):
        with pytest.raises(service.GuildError, match="can't be added"):
            await service.add_team(db, w.hero["Vanguard"], team_id, SIZE)


async def test_a_team_is_added_to_a_party_that_is_apart_in_a_town_as_a_group_of_its_own(db):
    w = await a_world(db)
    await towns.enter_town(db, Towns(), w.party["Vanguard"])
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE)
    view = await towns.view(db, w.team["Rearguard"])
    assert view["in_town"] and not view["waiting"]
    assert await towns.ready(db, Towns(), w.team["Vanguard"]) == "waiting", "the new team is not ready yet"
    assert await towns.ready(db, Towns(), w.team["Rearguard"]) == "reformed"
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"], w.team["Rearguard"]]


# --- asking an open party ------------------------------------------------------------------------------------------------

async def test_only_open_parties_of_allies_can_be_asked_once_each(db):
    w = await a_world(db)
    scouts = w.hero["Scouts"]
    with pytest.raises(service.GuildError, match="not looking"):
        await service.ask(db, GUILD, scouts, w.party["Vanguard"], SIZE)
    await service.set_open(db, w.party["Vanguard"], True)
    with pytest.raises(service.GuildError, match="only allies"):
        await service.ask(db, GUILD, scouts, w.party["Vanguard"], SIZE)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    assert await service.ask(db, GUILD, scouts, w.party["Vanguard"], SIZE) == {"party": w.party["Vanguard"], "team": w.team["Scouts"]}
    with pytest.raises(service.GuildError, match="asked already"):
        await service.ask(db, GUILD, scouts, w.party["Vanguard"], SIZE)
    with pytest.raises(service.GuildError, match="not looking"):
        await service.ask(db, GUILD, w.hero["Vanguard"], w.party["Vanguard"], SIZE)  # not its own party


async def test_a_players_own_open_party_can_be_asked_without_an_alliance(db):
    w = await a_world(db)
    await parties.play(db, w.team["Rearguard"], SIZE)
    await service.set_open(db, w.party["Vanguard"], True)
    assert (await service.ask(db, GUILD, w.hero["Rearguard"], w.party["Vanguard"], SIZE))["team"] == w.team["Rearguard"]


async def test_a_party_with_no_room_for_the_whole_team_cannot_be_asked(db):
    w = await a_world(db)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    await service.set_open(db, w.party["Vanguard"], True)
    with pytest.raises(service.GuildError, match="no room"):
        await service.ask(db, GUILD, w.hero["Scouts"], w.party["Vanguard"], 1)


async def test_the_search_lists_open_parties_on_the_map_and_hides_hidden_teams_names(db):
    w = await a_world(db)
    assert await service.open_parties(db, w.hero["Scouts"]) == []
    await service.set_open(db, w.party["Vanguard"], True)
    db.add(TeamProfile(team_id=w.team["Vanguard"], token="t" * 20, visible=False))
    await db.flush()
    found = await service.open_parties(db, w.hero["Scouts"])
    assert found == [{"id": w.party["Vanguard"], "heroes": 1, "teams": [{"name": None}]}]
    await db.execute(TeamProfile.__table__.update().values(visible=True))
    assert (await service.open_parties(db, w.hero["Scouts"]))[0]["teams"] == [{"name": "Vanguard"}]
    assert await service.open_parties(db, w.hero["Vanguard"]) == [], "never its own party"


# --- answering -------------------------------------------------------------------------------------------------------------

async def test_the_leader_accepts_a_request_and_the_team_changes_parties(db):
    w = await a_world(db)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    await service.set_open(db, w.party["Vanguard"], True)
    await service.ask(db, GUILD, w.hero["Scouts"], w.party["Vanguard"], SIZE)
    (waiting,) = await service.requests(db, w.hero["Vanguard"])
    assert waiting["team"] == "Scouts" and waiting["heroes"] == 1
    done = await service.answer(db, w.hero["Vanguard"], waiting["id"], True, SIZE)
    assert done == {"accepted": True, "team": w.team["Scouts"]}
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"], w.team["Scouts"]]
    assert await db.get(Party, w.party["Scouts"]) is None, "its own party was left"
    assert await db.scalar(select(func.count()).select_from(PartyRequest)) == 0
    assert await parties.leader_account(db, w.party["Vanguard"]) == w.mike.id


async def test_declining_removes_the_request_and_only_the_leader_answers(db):
    w = await a_world(db)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    await service.set_open(db, w.party["Vanguard"], True)
    await service.ask(db, GUILD, w.hero["Scouts"], w.party["Vanguard"], SIZE)
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE)
    request = (await service.requests(db, w.hero["Vanguard"]))[0]["id"]
    await parties.join_party(db, w.party["Vanguard"], w.team["Reserve"], SIZE)
    await parties.merge_parties(db, w.party["Scouts"], w.party["Vanguard"], SIZE, accepted_by=w.zed.id)  # Zed leads now
    with pytest.raises(service.GuildError, match="only the party's leader"):
        await service.answer(db, w.hero["Vanguard"], request, True, SIZE)
    assert await db.scalar(select(func.count()).select_from(PartyRequest)) == 0, "the merged party's requests went with it"


async def test_a_declined_request_is_gone(db):
    w = await a_world(db)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    await service.set_open(db, w.party["Vanguard"], True)
    await service.ask(db, GUILD, w.hero["Scouts"], w.party["Vanguard"], SIZE)
    request = (await service.requests(db, w.hero["Vanguard"]))[0]["id"]
    assert (await service.answer(db, w.hero["Vanguard"], request, False, SIZE))["accepted"] is False
    assert await service.requests(db, w.hero["Vanguard"]) == []
    with pytest.raises(service.GuildError, match="no such request"):
        await service.answer(db, w.hero["Vanguard"], request, True, SIZE)
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"]]


async def test_deleting_a_team_withdraws_its_requests(db):
    w = await a_world(db)
    await ally(db, w.team["Vanguard"], w.team["Scouts"])
    await service.set_open(db, w.party["Vanguard"], True)
    await service.ask(db, GUILD, w.hero["Scouts"], w.party["Vanguard"], SIZE)
    await heroes.delete_team(db, w.zed, w.team["Scouts"])
    assert await db.scalar(select(func.count()).select_from(PartyRequest)) == 0


# --- the dialog's open_party tag -----------------------------------------------------------------------------------------

async def say(db, hero, dialog):
    npc = await npcs.place_npc(db, "guildmaster", "Guildmaster", hero.map_id, hero.x, hero.y + 1, dialog)
    frame = await npcs.talk(db, NPCS, REACH, hero, npc.id)
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


async def test_only_the_leader_opens_or_closes_the_party_from_the_dialog(db):
    w = await a_world(db)
    dialog = "`open_party,on,no`Open.`jump,end``label,no`Only the leader."
    assert await say(db, w.hero["Vanguard"], dialog) == "Open."
    assert (await db.get(Party, w.party["Vanguard"])).open is True
    assert await say(db, w.hero["Vanguard"], "`open_party,off,no`Closed.`jump,end``label,no`Only the leader.") == "Closed."
    assert (await db.get(Party, w.party["Vanguard"])).open is False
    await parties.merge_parties(db, w.party["Scouts"], w.party["Vanguard"], SIZE, accepted_by=w.zed.id)
    assert await say(db, w.hero["Vanguard"], dialog) == "Only the leader.", "Zed leads the merged party now"


# --- the calls -------------------------------------------------------------------------------------------------------------

def sign_in(client, username):
    in_app_db(client, lambda db: create_account(db, username, PASSWORD, email=f"{username.lower()}@example.com", confirmed=True))
    return {"X-CSRF-Token": client.post("/api/login", json={"username": username, "password": PASSWORD}).json()["csrf_token"]}


def test_the_calls_add_a_team_through_the_conversation(app_client):
    client = app_client
    in_app_db(client, lambda db: load_content(db, SEED))
    headers = sign_in(client, "Mike")
    ids = {}
    for team, hero in (("Vanguard", "Aria"), ("Rearguard", "Bram")):
        made = testing.make_team(client, headers, team, heroes=(hero,))
        team_id, hero_id = made["id"], made["members"][0]["hero_id"]
        ids[team] = (team_id, hero_id)
    expect(client.post(f"/api/teams/{ids['Vanguard'][0]}/play", headers=headers), 200)
    aria = ids["Vanguard"][1]

    async def place(db):
        row = await db.get(Hero, aria)
        return (await npcs.place_npc(db, "guildmaster", "Guildmaster", row.map_id, row.x + 1, row.y, "Welcome.`add_team`Done.")).id

    npc = in_app_db(client, place)
    guild = f"/api/heroes/{aria}/dialog/guild"
    assert client.get(guild).status_code == 409, "not talking to anyone"
    expect(client.post(f"/api/heroes/{aria}/npcs/{npc}/talk", json={}, headers=headers), 200)
    seen = expect(client.get(guild), 200).json()
    assert seen["command"] == "add_team" and [team["name"] for team in seen["teams"]] == ["Rearguard"]
    assert client.post(f"{guild}/add-team", json={"team_id": ids["Rearguard"][0]}).status_code == 403, "no CSRF token"
    assert client.post(f"{guild}/ask", json={"party_id": 1}, headers=headers).status_code == 409, "the conversation waits on add_team"
    done = expect(client.post(f"{guild}/add-team", json={"team_id": ids["Rearguard"][0]}, headers=headers), 200).json()
    assert done["result"]["team"] == ids["Rearguard"][0]
    assert done["dialog"]["events"][0]["text"] == "Done." and done["dialog"]["ended"]
    assert client.post(f"{guild}/add-team", json={"team_id": ids["Rearguard"][0]}, headers=headers).status_code == 409, "one use per payment"


# --- the guest pass --------------------------------------------------------------------------------------------------------------

class Hourly(Guild):
    async def guest_pass(self, session, team_id):
        return GuestPass("guest", 3600)


async def test_a_team_without_a_guest_pass_stays_until_it_leaves(db):
    w = await a_world(db)
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE, GUILD)
    assert await db.scalar(select(func.count()).select_from(StandingStatus)) == 0


async def test_a_guest_team_leaves_the_party_when_its_pass_runs_out_and_the_others_stay(db, later):
    later(0)
    w = await a_world(db)
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE, Hourly())
    assert await standing.remaining(db, "team", w.team["Rearguard"], "guest") == 3600
    later(3599)
    assert await standing.sweep(db) == []
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"], w.team["Rearguard"]]
    later(3600)
    assert await standing.sweep(db) == [("team", w.team["Rearguard"], "guest", True)]
    assert await parties.team_ids(db, w.party["Vanguard"]) == [w.team["Vanguard"]]
    assert await parties.party_of(db, w.team["Rearguard"]) is None
    assert await db.scalar(select(func.count()).select_from(StandingStatus)) == 0


async def test_a_guest_who_leaves_early_takes_the_pass_along(db):
    w = await a_world(db)
    await service.add_team(db, w.hero["Vanguard"], w.team["Rearguard"], SIZE, Hourly())
    await parties.leave_party(db, w.team["Rearguard"])
    assert await db.scalar(select(func.count()).select_from(StandingStatus)) == 0


async def test_a_pass_is_a_teams_status_with_an_end_time(db):
    w = await a_world(db)
    with pytest.raises(standing.StandingError, match="guest pass"):
        await standing.place(db, "hero", w.hero["Vanguard"].id, "guest", 60, ends_party=True)
    with pytest.raises(standing.StandingError, match="guest pass"):
        await standing.place(db, "team", w.team["Vanguard"], "guest", None, ends_party=True)
