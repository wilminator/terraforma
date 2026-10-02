"""Fights in the database: the initial state, the log of rounds, the hash chain, replay and re-playing, on every database."""

import json

import pytest
from sqlalchemy import func, select, update

from terraforma.accounts.service import create_account
from terraforma.content.loader import load_content
from terraforma.fights import store
from terraforma.fights.build import hero_fighter, monster_fighter
from terraforma.fights.combatant import Command
from terraforma.fights.fight import build_fight
from terraforma.fights.models import FightActionRecord, FightParticipant, FightRecord
from terraforma.fights.resolve import fight_stream
from terraforma.fights.rules import Rules
from terraforma.fights.state import dehydrate, hydrate
from terraforma.fights.store import FightLogError, SequenceConflict, command_record
from terraforma.heroes import inventory, service
from terraforma.world.rng import WorldRng
from terraforma.world.start import ensure_start

from .test_fight_rules import busy_fight, fighter, sword

pytestmark = pytest.mark.anyio

RULES = Rules()
PASSWORD = "correct horse battery"


def attack(source, target):
    return command_record(source, Command.ATTACK_LEFT, 0, target)


async def start(db, **stats):
    """A duel on the hub: a sturdy fighter against another with a sword."""
    hub = await ensure_start(db)
    knight = fighter("Knight", HP=80, Strength=18, Accuracy=14, Speed=11)
    knight.inventory = [[sword(), 1]]
    knight.equipment = {"lhand": 0}
    brute = fighter("Brute", HP=80, Strength=16, Accuracy=12, Speed=9, **stats)
    fight = build_fight({0: {0: [knight]}, 1: {0: [brute]}})
    return hub, await store.create_fight(db, hub, fight)


DUEL = [attack((0, 0, 0), (1, 0, 0)), attack((1, 0, 0), (0, 0, 0))]


# --- the snapshot -----------------------------------------------------------------------------------------

def test_a_fight_survives_being_turned_into_json_and_back():
    fight = busy_fight()
    fight.parties[0].allies, fight.parties[1].enemies = {1}, {0}
    fight.get((0, 0, 0)).monster = "slime"
    raw = json.loads(json.dumps(dehydrate(fight)))
    again = hydrate(raw)
    assert dehydrate(again) == dehydrate(fight)
    assert again.parties[0].allies == {1} and again.parties[1].allies is None
    assert again.get((0, 0, 0)).monster == "slime" and again.get((0, 0, 0)).inventory[0][0].key == "sword"


# --- starting a fight -------------------------------------------------------------------------------------------

async def test_a_new_fight_is_stored_on_its_map_with_its_participants(db):
    hub, record = await start(db)
    await db.commit()
    assert (record.map_id, record.x, record.y) == (hub.id, 0, 0), "a fight stands somewhere"
    assert len(record.guid) == 32 and record.initial_state["parties"][0]["groups"][0]["characters"][0]["name"] == "Knight"
    rows = (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == record.id).order_by(FightParticipant.party))).all()
    assert [(row.party, row.group_index, row.character, row.name) for row in rows] == [(0, 0, 0, "Knight"), (1, 0, 0, "Brute")]
    assert (await store.find_by_guid(db, record.guid)).id == record.id
    assert await store.find_by_guid(db, "0" * 32) is None


async def test_every_fight_has_its_own_guid(db):
    _hub, first = await start(db)
    _hub, second = await start(db)
    assert first.guid != second.guid and first.id != second.id


# --- playing rounds -------------------------------------------------------------------------------------------------

async def test_rounds_are_logged_in_order_each_chained_to_the_one_before(db):
    _hub, record = await start(db)
    first = await store.play_round(db, record, DUEL, RULES)
    second = await store.play_round(db, record, DUEL, RULES)
    logged = await store.actions(db, record)
    assert [row.sequence for row in logged] == [1, 2]
    assert logged[0].previous_hash == store.initial_hash(record.initial_state)
    assert logged[1].previous_hash == logged[0].hash and logged[0].hash != logged[1].hash
    assert logged[0].events == [each.to_list() for each in first] and logged[1].events == [each.to_list() for each in second]
    assert logged[0].commands == DUEL


async def test_the_fight_now_is_the_initial_state_with_every_round_replayed(db):
    _hub, record = await start(db)
    events = [*await store.play_round(db, record, DUEL, RULES), *await store.play_round(db, record, DUEL, RULES)]
    fight, played = await store.load_state(db, record, RULES)
    assert played == 2
    damage = {0: 0, 1: 0}
    for each in events:
        if each.type.value == "Damage":
            damage[each.data[0]] += each.data[3]
    assert damage[0] > 0 and damage[1] > 0, "the duel did something"
    assert fight.get((0, 0, 0)).current["HP"] == 80 - damage[0]
    assert fight.get((1, 0, 0)).current["HP"] == 80 - damage[1]
    assert record.initial_state["parties"][0]["groups"][0]["characters"][0]["current"]["HP"] == 80, "the initial state is untouched"


async def test_anyone_without_a_command_defends(db):
    _hub, record = await start(db)
    events = await store.play_round(db, record, [attack((0, 0, 0), (1, 0, 0))], RULES)
    assert [each.type.value for each in events if each.type.value == "Turn"] == ["Turn"], "only the one with a command acted"
    assert not any(each.type.value == "Damage" and each.data[0] == 0 for each in events), "the defender was not attacked back"


async def test_a_round_is_played_from_its_own_stream_so_it_can_be_played_again(db):
    _hub, record = await start(db)
    first = await store.play_round(db, record, DUEL, RULES)
    # The same commands from the same state, with the same stream: the same round.
    from terraforma.fights.resolve import do_combat

    fight = hydrate(record.initial_state)
    store._set_commands(fight, DUEL)
    again = do_combat(fight, RULES, await store._stream(db, record, 1))
    assert again == first
    hub = await db.get(type(await ensure_start(db)), record.map_id)
    world = WorldRng(1)
    assert fight_stream(world, hub.name, record.id, 1).random() == fight_stream(world, hub.name, record.id, 1).random()
    assert fight_stream(world, hub.name, record.id, 1).random() != fight_stream(world, hub.name, record.id, 2).random()
    assert fight_stream(world, hub.name, record.id).random() != fight_stream(world, hub.name, record.id, 1).random()


# --- checking the log -----------------------------------------------------------------------------------------------------

async def test_an_honest_log_verifies_shallow_and_deep_and_with_snapshots(db):
    _hub, record = await start(db)
    await store.play_round(db, record, DUEL, RULES, snapshot=True)
    await store.play_round(db, record, DUEL, RULES)
    await store.play_round(db, record, DUEL, RULES, snapshot=True)
    assert await store.verify(db, record, RULES) == 3
    assert await store.verify(db, record, RULES, deep=True) == 3
    assert await store.verify(db, FightRecord(id=0, initial_state={}, guid="", map_id=0), RULES) == 0, "no rounds: nothing to fault"


async def test_a_changed_round_breaks_the_chain_at_that_round(db):
    _hub, record = await start(db)
    for _ in range(3):
        await store.play_round(db, record, DUEL, RULES)
    row = (await store.actions(db, record))[1]
    tampered = [list(each) for each in row.events]
    tampered[0][0] = "Miss"
    await db.execute(update(FightActionRecord).where(FightActionRecord.fight_id == record.id, FightActionRecord.sequence == 2).values(events=tampered))
    await db.commit()
    with pytest.raises(FightLogError) as error:
        await store.verify(db, record, RULES)
    assert error.value.sequence == 2 and "changed" in error.value.reason


async def test_a_missing_round_is_noticed(db):
    _hub, record = await start(db)
    for _ in range(3):
        await store.play_round(db, record, DUEL, RULES)
    await db.execute(FightActionRecord.__table__.delete().where(FightActionRecord.fight_id == record.id, FightActionRecord.sequence == 2))
    await db.commit()
    with pytest.raises(FightLogError) as error:
        await store.verify(db, record, RULES)
    assert error.value.sequence == 2 and "found round 3" in error.value.reason


async def test_a_changed_starting_state_breaks_the_chain_from_the_first_round(db):
    _hub, record = await start(db)
    await store.play_round(db, record, DUEL, RULES)
    changed = json.loads(json.dumps(record.initial_state))
    changed["parties"][0]["groups"][0]["characters"][0]["base"]["Strength"] = 999
    await db.execute(update(FightRecord).where(FightRecord.id == record.id).values(initial_state=changed))
    await db.commit()
    await db.refresh(record)
    with pytest.raises(FightLogError) as error:
        await store.verify(db, record, RULES)
    assert error.value.sequence == 1 and "does not follow" in error.value.reason


async def test_a_forged_round_with_a_neat_hash_is_caught_by_playing_it_again(db):
    _hub, record = await start(db)
    await store.play_round(db, record, DUEL, RULES)
    last = (await store.actions(db, record))[0]
    forged = [command_record((0, 0, 0), Command.DEFEND), command_record((1, 0, 0), Command.DEFEND)]
    await db.execute(update(FightActionRecord).where(FightActionRecord.fight_id == record.id).values(
        commands=forged, hash=store.round_hash(last.previous_hash, 1, forged, last.events)))
    await db.commit()
    assert await store.verify(db, record, RULES) == 1, "the hashes alone can't tell"
    with pytest.raises(FightLogError) as error:
        await store.verify(db, record, RULES, deep=True)
    assert error.value.sequence == 1 and "different events" in error.value.reason


async def test_two_players_cannot_play_the_same_round(db, monkeypatch):
    _hub, record = await start(db)
    await store.play_round(db, record, DUEL, RULES)
    real = store.actions
    calls = []

    async def stale(session, wanted):
        calls.append(1)
        return [] if len(calls) == 1 else await real(session, wanted)

    monkeypatch.setattr(store, "actions", stale)
    with pytest.raises(SequenceConflict):
        await store.play_round(db, record, DUEL, RULES)
    monkeypatch.setattr(store, "actions", real)
    assert len(await store.actions(db, record)) == 1, "the first round stands, the clash left nothing behind"


# --- heroes and monsters ------------------------------------------------------------------------------------------------------

SEED = {
    "abilities": [{"key": "slash", "name": "Slash", "kind": "skill", "effect": {"effect": "hurt", "base": 4}}],
    "items": [
        {"key": "sword", "name": "Sword", "equip_slots": ["hand"], "stat_bonus": {"Strength": 3}},
        {"key": "potion", "name": "Potion", "one_use": True, "use_effect": {"effect": "heal", "base": 10}},
    ],
    "jobs": [{"key": "fighter", "name": "Fighter", "stat_growth": {"HP": 40, "MP": 5, "Speed": 10, "Accuracy": 12, "Strength": 12, "Dodge": 4, "Block": 6}, "abilities": ["slash"]}],
    "personalities": [{"key": "plain", "name": "Plain"}],
    "monsters": [{"key": "ogre", "name": "Ogre", "personality": "plain", "stats": {"HP": 60, "MP": 0, "Speed": 8, "Accuracy": 10, "Strength": 14, "Dodge": 2, "Block": 5},
                  "items": ["potion"], "equipment": ["sword"]}],
}


async def a_hero(db):
    await load_content(db, SEED)
    account = await create_account(db, "Mike", PASSWORD, email="mike@example.com", confirmed=True)
    hero = await service.create_hero(db, account, "Aria", "fighter")
    await inventory.add_item(db, hero, "sword", 1)
    await inventory.add_item(db, hero, "potion", 3)
    assert (await inventory.equip(db, hero, 0, 1)).slots == ("rhand",)
    return hero


async def test_a_hero_becomes_a_fighter_with_what_it_knows_carries_and_wields(db):
    hero = await a_hero(db)
    fighter_ = await hero_fighter(db, hero)
    assert (fighter_.name, fighter_.charid) == ("Aria", hero.id)
    assert fighter_.current["HP"] == fighter_.base["HP"] == 40
    assert [ability.key for ability in fighter_.abilities] == ["slash"]
    assert [(item.key, qty) for item, qty in fighter_.inventory] == [("sword", 1), ("potion", 3)]
    assert fighter_.equipment == {"rhand": 0} and fighter_.get_current(RULES, "Strength", Command.ATTACK_RIGHT) == 15


async def test_a_monster_becomes_a_fighter_with_its_gear_on(db):
    await load_content(db, SEED)
    ogre = await monster_fighter(db, "ogre")
    assert (ogre.name, ogre.monster, ogre.base["HP"]) == ("Ogre", "ogre", 60)
    assert [item.key for item, _qty in ogre.inventory] == ["potion", "sword"]
    assert ogre.equipment == {"lhand": 1}
    with pytest.raises(LookupError):
        await monster_fighter(db, "dragon")


async def test_a_hero_and_a_monster_fight_a_logged_verified_fight(db):
    hero = await a_hero(db)
    hub = await ensure_start(db)
    fight = build_fight({0: {0: [await hero_fighter(db, hero)]}, 1: {0: [await monster_fighter(db, "ogre")]}})
    record = await store.create_fight(db, hub, fight)
    rows = (await db.scalars(select(FightParticipant).where(FightParticipant.fight_id == record.id).order_by(FightParticipant.party))).all()
    assert (rows[0].hero_id, rows[0].monster_key, rows[1].hero_id, rows[1].monster_key) == (hero.id, None, None, "ogre")
    for _ in range(4):
        await store.play_round(db, record, [command_record((0, 0, 0), Command.ATTACK_RIGHT, 0, (1, 0, 0)),
                                            command_record((1, 0, 0), Command.ATTACK_LEFT, 0, (0, 0, 0))], RULES, snapshot=True)
    assert await store.verify(db, record, RULES, deep=True) == 4
    state, played = await store.load_state(db, record, RULES)
    assert played == 4 and (state.get((0, 0, 0)).current["HP"] < 40 or state.get((1, 0, 0)).current["HP"] < 60)
    assert await db.scalar(select(func.count()).select_from(FightActionRecord).where(FightActionRecord.fight_id == record.id)) == 4
