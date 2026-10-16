"""Quest instancing: private, shared and public quests, who can see and fight whom inside them, and the shared-instance PvP rule."""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from terraforma import wallclock
from terraforma.accounts.service import create_account
from terraforma.instances import service
from terraforma.instances.models import InstanceParty, QuestInstance
from terraforma.instances.service import InstanceError
from terraforma.models import Map
from terraforma.parties import service as parties
from terraforma.pvp.service import PvpError, start_pvp_fight
from terraforma.reach import service as reach_service
from terraforma.reach.hooks import TALK, Reach
from terraforma.fights.rules import Rules
from terraforma.world.start import ensure_start

from .test_pvp import Everywhere, Levelled, a_party, two_parties
from .test_reach import RULES as REACH_RULES

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

RULES = Levelled()


async def dungeon(db, name="crypt"):
    hub = await ensure_start(db)
    found = Map(world_id=hub.world_id, name=name, width=10, height=10)
    db.add(found)
    await db.flush()
    return found


async def third_party(db, level=1):
    zed = await create_account(db, "Yan", "correct horse battery", email="yan@example.com", confirmed=True)
    return zed, *await a_party(db, zed, "Rovers", "Dax", level)


async def test_a_public_quest_has_no_instance_and_does_not_move_the_party(db):
    _mike, _zed, _team, party, _foes = await two_parties(db)
    found = await dungeon(db)
    assert await service.enter(db, RULES, party.id, "rats", service.PUBLIC, found, 1, 1) is None
    assert await service.instance_of(db, party.id) is None
    assert party.map_id != found.id


async def test_a_private_quest_gives_each_party_an_instance_of_its_own(db):
    _mike, _zed, _team, party, foes = await two_parties(db)
    found = await dungeon(db)
    start = (party.map_id, party.x, party.y)
    one = await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 2, 3)
    other = await service.enter(db, RULES, foes.id, "rats", service.PRIVATE, found, 2, 3)
    assert one.id != other.id and one.capacity == 1
    assert (party.map_id, party.x, party.y) == (found.id, 2, 3) and (foes.map_id, foes.x, foes.y) == (found.id, 2, 3)
    assert await service.same_place(db, party.id, foes.id) is False, "two private instances on one tile do not meet"
    assert await service.leave(db, party.id) is True
    assert (party.map_id, party.x, party.y) == start, "leaving puts the party back where it came in"
    assert await db.get(QuestInstance, one.id) is None, "an instance nobody is in is gone"
    assert await service.leave(db, party.id) is False


async def test_a_shared_quest_groups_parties_that_come_close_in_time_up_to_the_cap(db):
    _mike, _zed, _team, party, foes = await two_parties(db)
    _yan, _yteam, third = await third_party(db)
    found = await dungeon(db)
    one = await service.enter(db, RULES, party.id, "boss", service.SHARED, found, 0, 0, capacity=2)
    two = await service.enter(db, RULES, foes.id, "boss", service.SHARED, found, 0, 0, capacity=2)
    assert two.id == one.id and await service.party_ids(db, one.id) == [party.id, foes.id]
    assert await service.same_place(db, party.id, foes.id) is True
    again = await service.enter(db, RULES, third.id, "boss", service.SHARED, found, 0, 0, capacity=2)
    assert again.id != one.id, "the cap is reached: a new instance starts"


async def test_a_shared_quest_prefers_the_instance_of_similar_strength(db):
    _mike, _zed, _team, weak, _foes = await two_parties(db, mine=1, theirs=10)
    _yan, _yteam, strong = await third_party(db, level=9)
    _z, _zteam, mid = await third_party_named(db, "Quin", "Zoe", 10)
    found = await dungeon(db)
    low = await service.enter(db, RULES, weak.id, "boss", service.SHARED, found, 0, 0, capacity=3)
    high = await service.enter(db, RULES, strong.id, "boss", service.SHARED, found, 0, 0, capacity=3)  # (joins low: the only open one)
    assert high.id == low.id
    await service.leave(db, weak.id)  # (low's instance now holds the strong party only)
    new = await service.enter(db, RULES, mid.id, "boss", service.SHARED, found, 0, 0, capacity=3)
    assert new.id == low.id, "level 10 is nearest to the level 9 party"


async def third_party_named(db, account_name, hero_name, level):
    account = await create_account(db, account_name, "correct horse battery", email=f"{account_name.lower()}@example.com", confirmed=True)
    return account, *await a_party(db, account, f"{account_name}Team", hero_name, level)


async def test_the_joining_time_runs_out(db):
    _mike, _zed, _team, party, foes = await two_parties(db)
    found = await dungeon(db)
    one = await service.enter(db, RULES, party.id, "boss", service.SHARED, found, 0, 0, join_window_seconds=60)
    one.joinable_until = wallclock.now() - timedelta(seconds=1)
    await db.flush()
    two = await service.enter(db, RULES, foes.id, "boss", service.SHARED, found, 0, 0)
    assert two.id != one.id


async def test_only_the_first_party_to_complete_wins_and_the_others_are_named(db):
    _mike, _zed, _team, party, foes = await two_parties(db)
    found = await dungeon(db)
    instance = await service.enter(db, RULES, party.id, "boss", service.SHARED, found, 0, 0)
    await service.enter(db, RULES, foes.id, "boss", service.SHARED, found, 0, 0)
    assert await service.complete(db, foes.id) == [party.id]
    assert (instance.status, instance.winner_party_id) == ("won", foes.id)
    with pytest.raises(InstanceError, match="already completed"):
        await service.complete(db, party.id)
    assert await service.party_ids(db, instance.id) == [party.id, foes.id], "the others may stay and explore"
    _yan, _yteam, late = await third_party(db)
    fresh = await service.enter(db, RULES, late.id, "boss", service.SHARED, found, 0, 0)
    assert fresh.id != instance.id, "a won instance takes no one new"


async def test_a_party_outside_an_instance_cannot_complete(db):
    _mike, _zed, _team, party, _foes = await two_parties(db)
    with pytest.raises(InstanceError, match="not in an instance"):
        await service.complete(db, party.id)


async def test_entering_is_refused_for_a_bad_mode_a_party_already_in_one_or_a_bad_tile(db):
    _mike, _zed, _team, party, _foes = await two_parties(db)
    found = await dungeon(db)
    with pytest.raises(InstanceError, match="not 'solo'"):
        await service.enter(db, RULES, party.id, "rats", "solo", found, 0, 0)
    with pytest.raises(InstanceError, match="not on the quest's map"):
        await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 10, 0)
    await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)
    with pytest.raises(InstanceError, match="already in an instance"):
        await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)


async def test_a_party_in_a_town_cannot_go_on_a_quest(db):
    from terraforma.towns import service as towns
    from terraforma.towns.hooks import Towns

    _mike, _zed, _team, party, _foes = await two_parties(db)
    found = await dungeon(db)
    await towns.enter_town(db, Towns(), party.id)
    with pytest.raises(InstanceError, match="in a town"):
        await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)


async def test_a_party_that_goes_leaves_its_instance_and_an_empty_one_goes_too(db):
    _mike, _zed, team, party, _foes = await two_parties(db)
    found = await dungeon(db)
    instance = await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)
    party.map_id, party.x, party.y = found.id, 0, 0
    await parties.leave_party(db, team.id)
    assert await db.scalar(select(func.count()).select_from(InstanceParty)) == 0
    assert await db.get(QuestInstance, instance.id) is None


async def test_parties_in_different_instances_cannot_merge(db):
    _mike, _zed, _team, party, foes = await two_parties(db)
    found = await dungeon(db)
    await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)
    foes.map_id, foes.x, foes.y = found.id, 0, 0
    with pytest.raises(parties.PartyError, match="different quest instances"):
        await parties.merge_parties(db, party.id, foes.id, 20)


async def test_the_nearby_list_shows_only_the_parties_of_ones_own_instance(db):
    mike, _zed, team, party, foes = await two_parties(db)
    found = await dungeon(db)
    for each in (party, foes):
        each.map_id, each.x, each.y = found.id, 0, 0
    from terraforma.heroes.models import Hero

    hero = (await db.scalars(select(Hero).where(Hero.account_id == mike.id))).first()
    hero.map_id, hero.x, hero.y = found.id, 0, 0
    listing = await reach_service.nearby(db, Reach(), REACH_RULES, hero, TALK)
    assert [entry["id"] for entry in listing["nearby"]] == [foes.id], "out in the world they meet"
    await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)
    listing = await reach_service.nearby(db, Reach(), REACH_RULES, hero, TALK)
    assert listing["nearby"] == [], "alone in a private instance"


async def test_pvp_needs_the_same_instance(db):
    mike, _zed, team, party, foes = await two_parties(db)
    found = await dungeon(db)
    foes.map_id, foes.x, foes.y = found.id, 0, 0
    await service.enter(db, RULES, party.id, "rats", service.PRIVATE, found, 0, 0)
    with pytest.raises(PvpError, match="not here"):
        await start_pvp_fight(db, Everywhere(), RULES, mike, team.id, foes.id)


async def test_parties_of_one_shared_instance_fight_whatever_their_strength(db):
    mike, _zed, team, party, foes = await two_parties(db, mine=1, theirs=1)
    found = await dungeon(db)
    await service.enter(db, RULES, party.id, "boss", service.SHARED, found, 0, 0)
    await service.enter(db, RULES, foes.id, "boss", service.SHARED, found, 0, 0)
    from terraforma.heroes.models import Hero

    for hero in (await db.scalars(select(Hero))).all():
        hero.level = 1 if hero.name == "Aria" else 20  # Aria's party is far stronger, so the window would refuse it
    assert await service.in_same_shared_instance(db, party.id, foes.id) is True
    assert await start_pvp_fight(db, Everywhere(), RULES, mike, team.id, foes.id) is not None


async def test_the_window_still_applies_outside_a_shared_instance_and_the_place_still_counts(db):
    mike, _zed, team, party, foes = await two_parties(db, mine=10, theirs=1)
    with pytest.raises(PvpError, match="too weak"):
        await start_pvp_fight(db, Everywhere(), RULES, mike, team.id, foes.id)
    assert await service.in_same_shared_instance(db, party.id, foes.id) is False
    assert Rules().may_fight_in_shared_instance(True) is None
    assert Rules().may_fight_in_shared_instance(False) is not None
