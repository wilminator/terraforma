"""Standing statuses: placed by a dialog on a hero, a team or a party, ending at a wall-clock time, going into fights as
tokens, and out of reach of a buff-cancelling effect when unremovable. On every database."""

import json

import pytest
from sqlalchemy import func, select

from terraforma import housekeeping, wallclock
from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.fights import live, specs, store
from terraforma.fights.fight import build_fight
from terraforma.fights.rules import Rules
from terraforma.fights.specs import EffectSpec
from terraforma.fights.state import dehydrate, hydrate
from terraforma.fights.status import BAD, GOOD, StatusSpec
from terraforma.heroes import service as heroes
from terraforma.npcs import service
from terraforma.npcs.hooks import Npcs
from terraforma.reach.hooks import Reach
from terraforma.parties import service as parties
from terraforma.settings import Settings
from terraforma.standing import service as standing
from terraforma.standing.models import HERO, PARTY, TEAM, StandingStatus
from terraforma import testing
from terraforma.testing import in_app_db

from .helpers import expect
from .test_fight_rules import fighter
from .test_fight_store import SEED as FIGHT_SEED
from .test_statuses import A, B, bear, item, play, use

pytestmark = pytest.mark.anyio

PASSWORD = "correct horse battery"
RULES = Rules()
NPCS = Npcs()
REACH = Reach()
SEED = {
    **FIGHT_SEED,
    "statuses": [
        {"key": "ward", "name": "Ward", "kind": "good"},
        {"key": "hex", "name": "Hex", "kind": "bad"},
    ],
}


class Party:
    """Mike's Vanguard (Aria) and Rearguard (Bram) in one party."""


async def a_party(db):
    await load_content(db, SEED)
    mike = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    out = Party()
    out.teams, out.heroes, out.mike = [], [], mike
    for team_name, hero_name in (("Vanguard", "Aria"), ("Rearguard", "Bram")):
        team = await heroes.create_team(db, mike, team_name)
        hero = await heroes.create_hero(db, mike, hero_name, "fighter")
        await heroes.add_to_team(db, mike, team.id, hero.id)
        out.teams.append(team.id)
        out.heroes.append(hero)
    out.party = await parties.create_party(db, out.teams[0], 20)
    await parties.join_party(db, out.party.id, out.teams[1], 20)
    await db.flush()
    return out


async def rows(db):
    return (await db.scalar(select(func.count()).select_from(StandingStatus)))


async def npc_says(db, hero, dialog):
    npc = await service.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x, hero.y + 1, dialog)
    frame = await service.talk(db, NPCS, REACH, hero, npc.id)
    assert frame["ended"], frame
    return "".join(event["text"] for event in frame["events"] if event["type"] == "text")


# --- placing and reading ------------------------------------------------------------------------------------------------

async def test_a_status_ends_at_a_wall_clock_time_the_seconds_give_and_is_gone_after_it(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, HERO, p.heroes[0].id, "ward", 600)
    assert await standing.has(db, HERO, p.heroes[0].id, "ward")
    assert await standing.remaining(db, HERO, p.heroes[0].id, "ward") == 600
    later(599)
    assert await standing.has(db, HERO, p.heroes[0].id, "ward") and await standing.remaining(db, HERO, p.heroes[0].id, "ward") == 1
    later(600)
    assert not await standing.has(db, HERO, p.heroes[0].id, "ward"), "ended"
    assert await standing.of_hero(db, p.heroes[0]) == []


async def test_a_status_without_seconds_lasts_until_removed_and_placing_again_renews_it(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, TEAM, p.teams[0], "ward", None)
    later(10 * 365 * 24 * 3600)
    assert await standing.has(db, TEAM, p.teams[0], "ward") and await standing.remaining(db, TEAM, p.teams[0], "ward") is None
    await standing.place(db, TEAM, p.teams[0], "ward", 60, unremovable=True)
    assert await rows(db) == 1, "renewed, not doubled"
    assert await standing.remaining(db, TEAM, p.teams[0], "ward") == 60
    assert (await standing.of_hero(db, p.heroes[0]))[0]["unremovable"] is True


async def test_a_bad_target_status_or_length_is_refused(db):
    p = await a_party(db)
    for args, text in (
        (("planet", 1, "ward", 5), "one of"),
        ((HERO, p.heroes[0].id, "nope", 5), "no status"),
        ((HERO, p.heroes[0].id, "ward", 0), "at least a second"),
        ((HERO, p.heroes[0].id, "ward", 10**10), "ten years"),
    ):
        with pytest.raises(standing.StandingError, match=text):
            await standing.place(db, *args)
    assert await rows(db) == 0


async def test_a_hero_is_under_its_own_its_teams_and_its_partys_statuses(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, HERO, p.heroes[0].id, "ward", 100)
    await standing.place(db, TEAM, p.teams[0], "hex", 200)
    await standing.place(db, PARTY, p.party.id, "ward", None)
    await standing.place(db, HERO, p.heroes[1].id, "hex", 100)
    seen = [(each["on"], each["status"], each["seconds_left"]) for each in await standing.of_hero(db, p.heroes[0])]
    assert seen == [("hero", "ward", 100), ("team", "hex", 200), ("party", "ward", None)]
    assert [(each["on"], each["status"]) for each in await standing.of_hero(db, p.heroes[1])] == [("hero", "hex"), ("party", "ward")]


async def test_the_sweep_removes_what_has_ended_and_says_what(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, TEAM, p.teams[1], "ward", 10)
    await standing.place(db, TEAM, p.teams[0], "ward", 100)
    await standing.place(db, TEAM, p.teams[0], "hex", None)
    later(50)
    assert await standing.sweep(db) == [(TEAM, p.teams[1], "ward", False)]
    assert await rows(db) == 2
    assert await standing.sweep(db) == []


async def test_housekeeping_clears_ended_statuses(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, HERO, p.heroes[0].id, "ward", 10)
    await standing.place(db, HERO, p.heroes[1].id, "ward", 1000)
    later(100)
    assert await housekeeping.ended_standing_statuses(db, wallclock.timestamp(), Settings(session_secret="x" * 32)) == 1
    assert await rows(db) == 1


async def test_a_deleted_hero_team_or_party_takes_its_statuses_along(db):
    p = await a_party(db)
    for kind, target in ((HERO, p.heroes[1].id), (TEAM, p.teams[1]), (PARTY, p.party.id)):
        await standing.place(db, kind, target, "ward", None)
    await heroes.delete_hero(db, p.mike, p.heroes[1].id)
    assert await rows(db) == 2
    await heroes.delete_team(db, p.mike, p.teams[1])
    assert await rows(db) == 1
    await parties.leave_party(db, p.teams[0])
    assert await rows(db) == 0, "the party is empty and gone"


# --- the dialog ---------------------------------------------------------------------------------------------------------

async def test_the_dialog_places_a_status_from_seconds_and_tests_for_it(db, later):
    later(0)
    p = await a_party(db)
    said = await npc_says(db, p.heroes[0], "`add_status,team,ward,3600,removable,no`Blessed.`jump,end``label,no`Nothing.")
    assert said == "Blessed."
    assert await standing.remaining(db, TEAM, p.teams[0], "ward") == 3600
    assert await npc_says(db, p.heroes[0], "`has_status,team,ward,has,no`Yes.`jump,end``label,no`No.") == "Yes."
    assert await npc_says(db, p.heroes[0], "`has_status,hero,ward,has,no`Yes.`jump,end``label,no`No.") == "No."
    assert await npc_says(db, p.heroes[0], "`has_status,hero,ward,lacks,no`Lacks.`jump,end``label,no`Has.") == "Lacks."
    await npc_says(db, p.heroes[1], "`add_status,party,hex,0,locked,no`Cursed.`jump,end``label,no`Nothing.")
    row = await db.scalar(select(StandingStatus).where(StandingStatus.target_kind == PARTY))
    assert (row.ends_at, row.unremovable) == (None, True), "zero seconds: until removed; locked: unremovable"


async def test_a_status_for_a_target_the_hero_does_not_have_takes_the_label_branch(db):
    await load_content(db, SEED)
    loner = await create_account(db, "Loner", PASSWORD, email="loner@example.com", confirmed=True)
    hero = await heroes.create_hero(db, loner, "Dax", "fighter")
    await db.flush()
    assert await npc_says(db, hero, "`add_status,team,ward,60,removable,no`Done.`jump,end``label,no`You have no team.") == "You have no team."
    assert await rows(db) == 0


async def test_a_status_the_content_lacks_ends_the_conversation_with_the_reason(db):
    p = await a_party(db)
    with pytest.raises(service.NpcError, match="no status"):
        await npc_says(db, p.heroes[0], "`add_status,hero,nope,60,removable,no`Done.`jump,end``label,no`No.")


# --- the call -----------------------------------------------------------------------------------------------------------

def test_the_call_lists_the_statuses_a_hero_is_under(app_client):
    client = app_client
    in_app_db(client, lambda db: load_content(db, SEED))
    in_app_db(client, lambda db: create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True))
    headers = {"X-CSRF-Token": client.post("/api/login", json={"username": "Mike", "password": PASSWORD}).json()["csrf_token"]}
    hero = testing.make_hero(client, headers, "Aria")["id"]
    assert expect(client.get(f"/api/heroes/{hero}/statuses"), 200).json() == []
    in_app_db(client, lambda db: standing.place(db, HERO, hero, "ward", None, unremovable=True))
    assert expect(client.get(f"/api/heroes/{hero}/statuses"), 200).json() == [{"on": "hero", "status": "ward", "unremovable": True, "seconds_left": None}]
    assert client.get(f"/api/heroes/{hero + 1000}/statuses").status_code == 404


# --- fights -------------------------------------------------------------------------------------------------------------

async def started(db, p):
    team = await db.get(heroes.Team, p.teams[0])
    record = await live.start_team_fight(db, team, ["rat"], RULES)
    fight, _played = await store.load_state(db, record, RULES)
    return fight.get((0, 0, 0))


async def test_a_fight_starts_with_the_standing_statuses_on_the_fighters_as_tokens(db):
    p = await a_party(db)
    await standing.place(db, HERO, p.heroes[0].id, "ward", 60)
    await standing.place(db, TEAM, p.teams[0], "hex", None, unremovable=True)
    await standing.place(db, TEAM, p.teams[0], "ward", None, unremovable=True)  # the same status from a second place: one token
    await db.flush()
    aria = await started(db, p)
    assert sorted((token.spec.key, token.unremovable, token.remaining) for token in aria.tokens) == [("hex", True, None), ("ward", True, None)]
    assert all(token.source == (0, 0, 0) for token in aria.tokens)


async def test_an_ended_status_does_not_go_into_the_fight(db, later):
    later(0)
    p = await a_party(db)
    await standing.place(db, HERO, p.heroes[0].id, "ward", 60)
    later(61)
    assert (await started(db, p)).tokens == []


# --- unremovable --------------------------------------------------------------------------------------------------------

def test_a_buff_cancelling_effect_leaves_an_unremovable_token_on():
    bad, good = StatusSpec("hex", "Hex", BAD, 5), StatusSpec("ward", "Ward", GOOD, 5)
    a, b = fighter("A"), fighter("B")
    a.inventory = [[item("cleanse", EffectSpec(specs.REMOVE_BAD_STATUS)), 1], [item("purge", EffectSpec(specs.REMOVE_GOOD_STATUS)), 1]]
    fight = build_fight({0: {0: [a]}, 1: {0: [b]}}, {each.key: each for each in (bad, good)})
    bear(b, bad, duration=5).unremovable = True
    bear(b, good, duration=5).unremovable = True
    use(a, 0, B)
    play(fight, [100])
    use(a, 1, B)
    play(fight, [100])
    assert sorted(token.spec.key for token in b.tokens) == ["hex", "ward"], "neither cleanse nor purge took them off"
    next(token for token in b.tokens if token.spec.key == "hex").unremovable = False
    use(a, 0, B)
    play(fight, [100])
    assert [token.spec.key for token in b.tokens] == ["ward"], "a hex that may come off does"


def test_a_stored_fight_keeps_its_old_shape_and_the_flag_is_written_only_when_set():
    spec = StatusSpec("ward", "Ward", GOOD, 5)
    a, b = fighter("A"), fighter("B")
    fight = build_fight({0: {0: [a]}, 1: {0: [b]}}, {"ward": spec})
    bear(a, spec, duration=5)
    raw = json.loads(json.dumps(dehydrate(fight)))
    token = raw["parties"][0]["groups"][0]["characters"][0]["tokens"][0]
    assert "unremovable" not in token, "so the hash of a fight stored before the flag stays the same"
    a.tokens[0].unremovable = True
    raw = json.loads(json.dumps(dehydrate(fight)))
    assert raw["parties"][0]["groups"][0]["characters"][0]["tokens"][0]["unremovable"] is True
    assert hydrate(raw).get((0, 0, 0)).tokens[0].unremovable is True
