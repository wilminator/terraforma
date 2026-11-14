"""A place in ruins closes: its NPCs can't be reached while its settlement is not live, and a party waiting in it is put back together outside."""

import copy

import pytest
from sqlalchemy import select

from terraforma.content.loader import load_content
from terraforma.models import Map
from terraforma.npcs import service as npcs
from terraforma.npcs.models import Npc
from terraforma.reach.hooks import TALK, Reach
from terraforma.realms import service
from terraforma.realms.models import Settlement
from terraforma.towns import service as towns_service
from terraforma.towns.hooks import Towns

from .test_maps import SEED as MAPS_SEED
from .test_npcs import a_hero
from .test_towns import party_of_three

pytestmark = [pytest.mark.anyio, pytest.mark.heroes]

REACH = Reach()


async def a_place(db, hero, **place):
    """A live settlement of 'ardent' on the hero's map, with a keeper that belongs to it standing next to the hero (no counter)."""
    realm = await service.create_realm(db, (await db.get(Map, hero.map_id)).world_id, "ardent", "Ardent")
    town = await service.add_settlement(db, realm, hero.map_id, "lost", "Lost", x=hero.x + 5, y=hero.y + 5, **place)
    keeper = await npcs.place_npc(db, "keeper", "Keeper", hero.map_id, hero.x + 1, hero.y, "Hello.", [], town.id)
    return town, keeper


async def test_the_people_of_a_place_can_be_reached_only_while_it_is_live(db):
    hero = await a_hero(db)
    town, keeper = await a_place(db, hero)
    assert await REACH.npc(db, TALK, hero, keeper) is None
    await service.fall(db, town)
    assert await REACH.npc(db, TALK, hero, keeper) == "that place lies in ruins"
    assert [each.key for each in await npcs.npcs_here(db, REACH, hero)] == []
    await service.found(db, town)
    assert await REACH.npc(db, TALK, hero, keeper) is None


async def test_a_dormant_place_is_closed_too_and_an_npc_with_no_place_is_not_affected(db):
    hero = await a_hero(db)
    town, keeper = await a_place(db, hero, state="dormant")
    free = await npcs.place_npc(db, "free", "Free", hero.map_id, hero.x + 1, hero.y + 1, "Hi.", [])
    assert await REACH.npc(db, TALK, hero, keeper) == "that place lies in ruins"
    assert await REACH.npc(db, TALK, hero, free) is None
    await service.found(db, town)
    assert await REACH.npc(db, TALK, hero, keeper) is None


async def test_a_party_waiting_in_a_town_that_falls_is_put_back_together(db):
    hero = await a_hero(db)
    town, _keeper = await a_place(db, hero)
    party, _teams = await party_of_three(db)
    party.map_id, party.x, party.y = town.map_id, town.x, town.y
    await db.flush()
    await towns_service.enter_town(db, Towns(), party.id)
    await service.fall(db, town)
    assert not await towns_service.is_suspended(db, party.id), "the fallen town lets its waiting party go"


async def test_a_party_in_another_town_is_left_alone_when_a_town_falls(db):
    hero = await a_hero(db)
    town, _keeper = await a_place(db, hero)
    party, _teams = await party_of_three(db)
    party.map_id, party.x, party.y = town.map_id, town.x + 1, town.y
    await db.flush()
    await towns_service.enter_town(db, Towns(), party.id)
    await service.fall(db, town)
    assert await towns_service.is_suspended(db, party.id)


async def test_the_seed_ties_an_npc_to_a_settlement_and_a_missing_one_is_refused(db):
    data = copy.deepcopy(MAPS_SEED)
    data["realms"] = [{"key": "ardent", "name": "Ardent"}]
    data["settlements"] = [{"key": "capital", "name": "Ardent City", "realm": "ardent", "map": "meadow", "tier": 3}]
    data["npcs"] = [{"key": "clerk", "name": "Clerk", "map": "meadow", "x": 1, "y": 1, "dialog": "Hello.", "settlement": "capital"}]
    await load_content(db, data)
    clerk = await db.scalar(select(Npc).where(Npc.key == "clerk"))
    capital = await db.scalar(select(Settlement).where(Settlement.key == "capital"))
    assert clerk.settlement_id == capital.id
    data["npcs"][0]["settlement"] = "nowhere"
    with pytest.raises(npcs.NpcError, match="nowhere"):
        await load_content(db, data)
