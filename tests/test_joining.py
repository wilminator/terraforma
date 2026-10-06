"""Joining a running fight: the hub's offer, and a party becoming the next party of a fight, on every database."""

import pytest
from sqlalchemy import func, select

from terraforma.fights import joining, live, store
from terraforma.fights.combatant import Command
from terraforma.fights.events import EventType
from terraforma.fights.models import FightActionRecord, FightJoinRecord, FightParticipant, FightRecord, JoinOffer
from terraforma.fights.replay import apply_events
from terraforma.fights.rules import Rules
from terraforma.fights.state import dehydrate, hydrate
from terraforma.game import Game
from terraforma.heroes import service
from terraforma.heroes.models import Team
from terraforma.maps import walking
from terraforma.models import Account, World
from terraforma.parties import service as parties
from terraforma.relations.hooks import Change, Ref, Relations
from terraforma.relations.models import Relationship
from terraforma.relations.service import apply as apply_relation
from terraforma.testing import in_app_db
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns
from terraforma.towns.service import TownError
from terraforma.world.start import ensure_start

from .helpers import expect
from .test_towns import MONSTERS, PASSWORD, SEED, TOWNS, account, count, sign_in, with_monsters

pytestmark = pytest.mark.anyio


class Sure(Rules):
    """The hub always offers a running fight (when one suits)."""

    join_chance = 1.0


class Never(Rules):
    join_chance = 0.0


async def a_team(db, owner, name, hero):
    team = await service.create_team(db, owner, name)
    await service.add_to_team(db, owner, team.id, (await service.create_hero(db, owner, hero, "fighter")).id)
    return team


async def scene(db):
    """Zed's Scouts (Cleo) are fighting a slime; Mike's Vanguard (Aria) stands at the hub, about to leave it. Returns Mike, Zed, the
    fight (a record), Mike's team and his party."""
    await with_monsters(db)
    hub = await ensure_start(db)
    (await db.get(World, hub.world_id)).seed = 20261006  # a world's seed is random: fix it so the dice never decide a test
    mike, zed = await account(db), await account(db, "Zed")
    scouts = await a_team(db, zed, "Scouts", "Cleo")
    host = await live.start_team_fight(db, scouts, ["slime"], Never())
    vanguard = await a_team(db, mike, "Vanguard", "Aria")
    party = await parties.create_party(db, vanguard.id, 20)
    await towns_service.settle(db, TOWNS, party.id)
    return mike, zed, host, vanguard, party


async def offered(db, rules=None):
    """The scene, with Mike's party leaving the hub and being offered Zed's fight."""
    mike, zed, host, vanguard, party = await scene(db)
    rules = rules or Sure()
    assert await towns_service.leave(db, TOWNS, rules, vanguard.id) == ("reformed", None)
    assert await joining.offer_of(db, party.id) is not None
    return mike, zed, host, vanguard, party, rules


# --- the offer -------------------------------------------------------------------------------------------------------

async def test_the_hub_offers_a_running_fight_instead_of_starting_one(db):
    _mike, _zed, host, vanguard, party, _rules = await offered(db)
    assert (await joining.offer_of(db, party.id)).fight_id == host.id
    assert await count(db, FightRecord) == 1, "no fight starts until the leader answers"
    assert await towns_service.join_offer(db, vanguard.id) is True


async def test_the_chance_is_the_games_and_a_miss_starts_the_fight_as_usual(db):
    _mike, _zed, _host, vanguard, party = await scene(db)
    result, fight = await towns_service.leave(db, TOWNS, Never(), vanguard.id)
    assert result == "reformed" and fight is not None and fight.id != 1
    assert await joining.offer_of(db, party.id) is None and await count(db, FightRecord) == 2


async def test_the_roll_is_the_worlds_so_the_same_party_gets_the_same_answer(db):
    class Half(Rules):
        join_chance = 0.5

    _mike, _zed, _host, vanguard, party = await scene(db)
    first = await towns_service.leave(db, TOWNS, Half(), vanguard.id)
    again_party = await parties.get_party(db, party.id)
    assert (first[1] is None) == (await joining.offer_of(db, again_party.id) is not None)


async def test_no_offer_when_no_fight_is_running(db):
    _mike, _zed, host, vanguard, party = await scene(db)
    host.finished = True
    _result, fight = await towns_service.leave(db, TOWNS, Sure(), vanguard.id)
    assert fight is not None and await joining.offer_of(db, party.id) is None


async def test_no_offer_for_a_fight_that_is_full(db):
    class Two(Sure):
        max_fight_parties = 2

    _mike, _zed, _host, vanguard, party = await scene(db)
    _result, fight = await towns_service.leave(db, TOWNS, Two(), vanguard.id)
    assert fight is not None and await joining.offer_of(db, party.id) is None


async def test_no_offer_for_a_fight_out_of_the_partys_range(db):
    class Far(Sure):
        def may_join_fight(self, joiner_pxp, fight_pxps):
            return "too far"

    _mike, _zed, _host, vanguard, party = await scene(db)
    _result, fight = await towns_service.leave(db, TOWNS, Far(), vanguard.id)
    assert fight is not None and await joining.offer_of(db, party.id) is None


def test_the_default_range_is_the_pvp_window_and_a_stronger_fight_is_always_fine():
    rules = Rules()
    assert rules.may_join_fight(100, [100]) is None and rules.may_join_fight(100, [10, 85]) is None
    assert rules.may_join_fight(100, [84, 20]) is not None
    assert rules.may_join_fight(100, [5000]) is None


def test_a_party_counts_another_by_the_stances_of_its_teams():
    stance = Rules().party_stance
    assert [stance(each) for each in (["ally"], ["ally", "neutral"], ["enemy", "neutral"], ["ally", "enemy"], ["neutral"], [])] == [
        "ally", "ally", "enemy", "neutral", "enemy", "enemy"]


def test_a_teams_view_counts_by_its_band():
    relations = Relations()
    assert [relations.stance(score) for score in (-100, -40, 0, 40, 100)] == ["enemy", "enemy", "neutral", "ally", "ally"]


def test_fighting_parties_that_are_all_neutral_to_each_other_end_the_fight():
    from terraforma.fights.fight import Group, Party

    rules = Rules()
    fight = hydrate(dehydrate(store_fight()))
    assert rules.fight_is_over(fight) is False
    fight.parties[2] = Party({0: Group({0: fight.parties[0].groups[0].characters[0]})}, set(), set())
    fight.parties[1].groups[0].characters[0].current["HP"] = 0
    fight.parties[0].enemies, fight.parties[0].allies = set(), set()
    assert rules.fight_is_over(fight) is True, "the two left stand neutral: nobody is left to fight"


def store_fight():
    from terraforma.fights.combatant import Combatant
    from terraforma.fights.fight import build_fight

    def one(name):
        return Combatant(name=name, base={"HP": 10, "MP": 0}, current={"HP": 10, "MP": 0})

    return build_fight({0: {0: [one("A")]}, 1: {0: [one("B")]}})


# --- answering -------------------------------------------------------------------------------------------------------

async def test_declining_starts_the_partys_own_fight(db):
    mike, _zed, host, vanguard, party, rules = await offered(db)
    fight = await towns_service.decline_join(db, TOWNS, rules, mike.id, vanguard.id)
    assert fight is not None and fight.id != host.id and await joining.offer_of(db, party.id) is None
    heroes = set(await parties.hero_ids(db, party.id))
    assert heroes <= set((await db.scalars(select(FightParticipant.hero_id).where(FightParticipant.fight_id == fight.id))).all())


async def test_accepting_makes_the_party_the_next_party_of_that_fight(db):
    mike, zed, host, vanguard, party, rules = await offered(db)
    joined, fight = await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    assert joined is True and fight.id == host.id and await joining.offer_of(db, party.id) is None
    state, played = await store.load_state(db, host, rules)
    assert played == 0 and sorted(state.parties) == [0, 1, 2], "parties never merge: Mike's is the third"
    assert state.parties[2].teams == {vanguard.id: state.parties[2].teams[vanguard.id]}
    aria = (await parties.hero_ids(db, party.id))[0]
    rows = (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == host.id, FightParticipant.party == 2))).all()
    assert [(row.hero_id, row.name) for row in rows] == [(aria, "Aria")]
    assert await live.watchers(db, host) == {mike.id, zed.id}
    assert await count(db, FightRecord) == 1, "no fight of its own"


async def test_strangers_become_neutral_acquaintances_and_count_each_other_as_enemies(db):
    mike, _zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    scouts = await db.scalar(select(Team.id).where(Team.name == "Scouts"))
    rows = (await db.scalars(select(Relationship).order_by(Relationship.id))).all()
    assert sorted((row.subject_id, row.object_id, row.score) for row in rows) == sorted([(vanguard.id, scouts, 0), (scouts, vanguard.id, 0)])
    state, _played = await store.load_state(db, host, rules)
    assert rules.alignment(state, 2) == ({2}, {0, 1}), "the joiner is for itself"
    assert rules.alignment(state, 0) == ({0}, {1, 2}) and rules.alignment(state, 1) == ({1}, {0, 2})


async def test_a_friends_party_is_an_ally_but_only_one_way(db):
    mike, _zed, host, vanguard, party = await scene(db)
    scouts = await db.scalar(select(Team.id).where(Team.name == "Scouts"))
    await apply_relation(db, Relations(), Change(Ref("team", vanguard.id), Ref("team", scouts), score=50))  # Mike's team likes them
    await apply_relation(db, Relations(), Change(Ref("team", scouts), Ref("team", vanguard.id), score=-50))  # and they don't
    rules = Sure()
    await towns_service.leave(db, TOWNS, rules, vanguard.id)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    state, _played = await store.load_state(db, host, rules)
    assert rules.alignment(state, 2) == ({0, 2}, {1}), "Mike's party counts Zed's as an ally (and the monsters as enemies)"
    assert rules.alignment(state, 0) == ({0}, {1, 2}), "while Zed's counts Mike's as an enemy"
    assert (await db.scalar(select(func.count()).select_from(Relationship))) == 2, "the pair already had relationships: nothing new"


async def test_the_joiner_acts_from_the_next_round_and_the_round_starts_with_its_joining(db):
    mike, zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    cleo, aria, slime = (0, 0, 0), (2, 0, 0), (1, 0, 0)
    assert await live.submit_command(db, host, zed.id, cleo, Command.ATTACK_RIGHT, 0, slime, rules) == 1
    assert await live.everyone_committed(db, host, rules) is False, "the round waits for the joiner too"
    assert await live.submit_command(db, host, mike.id, aria, Command.ATTACK_RIGHT, 0, slime, rules) == 1
    assert await live.everyone_committed(db, host, rules) is True
    assert await count(db, FightJoinRecord) == 1
    result = await live.resolve_round(db, host, rules)
    assert result.events[0].type is EventType.PARTY_JOINED and result.events[0].data[0] == 2
    assert await count(db, FightJoinRecord) == 0, "the round took it into its own events"
    log = (await db.scalars(select(FightActionRecord).where(FightActionRecord.fight_id == host.id))).all()
    assert [event[0] for event in log[0].events].count("PartyJoined") == 1 and log[0].events[0][0] == "PartyJoined"
    state, played = await store.load_state(db, host, rules)
    assert played == 1 and sorted(state.parties) == [0, 1, 2]
    assert await store.verify(db, host, rules, deep=True) == 1, "the chain holds and the round plays out the same again"


async def test_the_joiner_earns_only_for_what_it_does_after_joining(db):
    mike, _zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    state, _played = await store.load_state(db, host, rules)
    assert all(not state.get(address).xp_debts for address in state.addresses() if address[0] == 2), "it brings no history"
    for _ in range(5):  # (a miss is a round with nothing owed)
        await live.submit_command(db, host, mike.id, (2, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0), rules)
        await live.resolve_round(db, host, rules)
        state, _played = await store.load_state(db, host, rules)
        debts = [debt for address in state.addresses() for debt in state.get(address).xp_debts]
        if any(tuple(debt[:3]) == (2, 0, 0) for debt in debts):
            break
    assert any(tuple(debt[:3]) == (2, 0, 0) for debt in debts), "what it did after joining is what it is owed for"


async def test_the_fight_runs_to_its_end_with_three_parties(db):
    mike, zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    for _ in range(60):
        if host.finished:
            break
        state, _played = await store.load_state(db, host, rules)
        slime = state.get((1, 0, 0)).alive(rules)  # the heroes are strangers, so enemies: once the slime is down they fight each other
        for owner, address, rival in ((zed, (0, 0, 0), (2, 0, 0)), (mike, (2, 0, 0), (0, 0, 0))):
            if state.get(address).alive(rules):
                await live.submit_command(db, host, owner.id, address, Command.ATTACK_RIGHT, 0, (1, 0, 0) if slime else rival, rules)
        await live.resolve_round(db, host, rules)
    state, played = await store.load_state(db, host, rules)
    assert state.over and host.finished
    assert await store.verify(db, host, rules, deep=True) == played


async def test_several_parties_can_join_one_after_another_before_a_round_plays(db):
    mike, zed, host, vanguard, party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    amy = await account(db, "Amy")
    rearguard = await a_team(db, amy, "Rearguard", "Bram")
    second = await parties.create_party(db, rearguard.id, 20)
    _record, number = await joining.join(db, rules, Relations(), host.id, second.id)
    assert number == 3
    state, _played = await store.load_state(db, host, rules)
    assert sorted(state.parties) == [0, 1, 2, 3] and rules.alignment(state, 3) == ({3}, {0, 1, 2})
    assert await count(db, FightJoinRecord) == 2
    with pytest.raises(live.Refused, match="full"):
        third = await parties.create_party(db, (await a_team(db, amy, "Third", "Cass")).id, 20)
        await joining.join(db, rules, Relations(), host.id, third.id)


async def test_a_fight_that_cannot_take_the_party_any_more_starts_its_own_fight_instead(db):
    mike, _zed, host, vanguard, party, rules = await offered(db)
    host.finished = True
    joined, fight = await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    assert joined is False and fight is not None and fight.id != host.id
    assert await joining.offer_of(db, party.id) is None


async def test_only_the_leader_answers_and_only_when_there_is_an_offer(db):
    mike, _zed, _host, vanguard, party = await scene(db)
    with pytest.raises(TownError, match="offered that party nothing"):
        await towns_service.accept_join(db, TOWNS, Sure(), Relations(), mike.id, vanguard.id)
    with pytest.raises(TownError, match="offered that party nothing"):
        await towns_service.decline_join(db, TOWNS, Sure(), mike.id, vanguard.id)
    await towns_service.leave(db, TOWNS, Sure(), vanguard.id)
    other = await a_team(db, await account(db, "Amy"), "Rearguard", "Bram")
    await parties.join_party(db, party.id, other.id, 20)
    with pytest.raises(TownError, match="only the party's leader"):
        await towns_service.accept_join(db, TOWNS, Sure(), Relations(), (await db.get(Team, other.id)).account_id, other.id)
    assert await joining.offer_of(db, party.id) is not None, "the refusal changed nothing"


async def test_a_party_with_an_offer_cannot_walk_or_pick_a_fight_until_it_answers(db):
    _mike, _zed, _host, _vanguard, party, _rules = await offered(db)
    with pytest.raises(walking.WalkError, match="offered the party a fight"):
        await walking._check_free(db, await parties.get_party(db, party.id))


async def test_an_offer_goes_with_its_party(db):
    _mike, _zed, _host, vanguard, party, _rules = await offered(db)
    await parties.leave_party(db, vanguard.id)
    assert await count(db, JoinOffer) == 0


async def test_joining_refuses_a_party_with_no_heroes_or_a_hero_already_fighting(db):
    mike, _zed, host, vanguard, party = await scene(db)
    rules = Sure()
    empty = await service.create_team(db, mike, "Empty")
    nobody = await parties.create_party(db, empty.id, 20)
    with pytest.raises(live.Refused, match="no heroes"):
        await joining.join(db, rules, Relations(), host.id, nobody.id)
    other = await live.start_party_fight(db, party.id, ["slime"], rules)
    with pytest.raises(live.Refused, match="already in a fight"):
        await joining.join(db, rules, Relations(), host.id, party.id)
    assert other.id != host.id


# --- the log ---------------------------------------------------------------------------------------------------------

async def test_a_stored_join_replays_into_the_same_fight(db):
    mike, zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    before, _played = await store.load_state(db, host, rules)
    event = (await store.pending_joins(db, host))[0]
    again = hydrate(host.initial_state)
    apply_events(again, rules, [event])
    assert dehydrate(again) == dehydrate(before)
    assert event.type is EventType.PARTY_JOINED and all(isinstance(key, str) for key in event.data[2])


async def test_a_changed_join_breaks_the_chain(db):
    mike, zed, host, vanguard, _party, rules = await offered(db)
    await towns_service.accept_join(db, TOWNS, rules, Relations(), mike.id, vanguard.id)
    await live.submit_command(db, host, mike.id, (2, 0, 0), Command.DEFEND, 0, (2, 0, 0), rules)
    await live.submit_command(db, host, zed.id, (0, 0, 0), Command.DEFEND, 0, (0, 0, 0), rules)
    await live.resolve_round(db, host, rules)
    row = await db.get(FightActionRecord, (host.id, 1))
    row.events = [event for event in row.events if event[0] != "PartyJoined"]
    await db.flush()
    with pytest.raises(store.FightLogError):
        await store.verify(db, host, rules)


# --- the calls -------------------------------------------------------------------------------------------------------

@pytest.fixture
def game(tmp_path):
    import json

    seed_dir = tmp_path / "seed"
    seed_dir.mkdir()
    for kind, content in {**SEED, **MONSTERS}.items():
        (seed_dir / f"{kind}.json").write_text(json.dumps(content))
    return Game(name="Test Game", seed_dir=seed_dir, rules=Sure())


def _set_up(app_client):
    """Zed's team fights a slime; Mike's team (made through the call) is in a party, apart in the hub's town."""
    mike = sign_in(app_client, "Mike")
    team = expect(app_client.post("/api/teams", json={"name": "Vanguard"}, headers=mike), 201).json()["id"]

    async def build(db):
        zed = await account(db, "Zed")
        scouts = await a_team(db, zed, "Scouts", "Cleo")
        host = await live.start_team_fight(db, scouts, ["slime"], Never())
        owner = await db.get(Account, (await db.get(Team, team)).account_id)
        await service.add_to_team(db, owner, team, (await service.create_hero(db, owner, "Aria", "fighter")).id)
        party = await parties.create_party(db, team, 20)
        await towns_service.settle(db, TOWNS, party.id)
        return host.id

    return mike, team, in_app_db(app_client, build)


def test_leaving_the_hub_says_when_a_fight_is_offered_and_the_leader_accepts(app_client):
    mike, team, host = _set_up(app_client)
    url = f"/api/teams/{team}/town"
    assert expect(app_client.get(f"{url}/join-offer"), 200).json() == {"offer": False}
    answer = expect(app_client.post(f"{url}/ready", json={}, headers=mike), 200).json()
    assert answer["result"] == "reformed" and answer["fight"] is None and answer["offer"] is True
    assert expect(app_client.get(f"{url}/join-offer"), 200).json() == {"offer": True}, "what the fight is is never said"
    expect(app_client.post(f"{url}/join-offer/accept", json={}), 403)  # no CSRF token
    joined = expect(app_client.post(f"{url}/join-offer/accept", json={}, headers=mike), 200).json()
    assert joined["joined"] is True and joined["fight"] == host
    expect(app_client.post(f"{url}/join-offer/accept", json={}, headers=mike), 409)  # answered
    expect(app_client.post(f"{url}/join-offer/decline", json={}, headers=mike), 409)
    mine = expect(app_client.get("/api/fights?running=true"), 200).json()
    assert [each["id"] for each in mine] == [host] and [hero["name"] for hero in mine[0]["heroes"]] == ["Aria"]
    assert {fighter["party"] for fighter in expect(app_client.get(f"/api/fights/{host}"), 200).json()["fighters"]} == {0, 1, 2}


def test_declining_over_the_call_starts_the_partys_own_fight(app_client):
    mike, team, host = _set_up(app_client)
    url = f"/api/teams/{team}/town"
    expect(app_client.post(f"{url}/ready", json={}, headers=mike), 200)
    declined = expect(app_client.post(f"{url}/join-offer/decline", json={}, headers=mike), 200).json()
    assert declined["joined"] is False and declined["fight"] not in (None, host)


def test_nobody_else_sees_or_answers_a_teams_offer(app_client):
    mike, team, _host = _set_up(app_client)
    expect(app_client.post(f"/api/teams/{team}/town/ready", json={}, headers=mike), 200)
    zed = {"X-CSRF-Token": app_client.post("/api/login", json={"username": "Zed", "password": PASSWORD}).json()["csrf_token"]}
    expect(app_client.get(f"/api/teams/{team}/town/join-offer"), 404)
    expect(app_client.post(f"/api/teams/{team}/town/join-offer/accept", json={}, headers=zed), 404)
    expect(app_client.post(f"/api/teams/{team}/town/join-offer/decline", json={"extra": 1}, headers=zed), 404)
