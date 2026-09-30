"""The models and the per-database helpers, on every database under test."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from terraforma.accounts.service import authenticate, create_account
from terraforma.db.dialect import same_text, upsert
from terraforma.models import Account, Fighter, Map, World, advance_clock

pytestmark = pytest.mark.anyio


async def test_a_world_gets_a_seed_and_starts_its_clock_at_zero(db):
    world = World(name="Terra")
    db.add(world)
    await db.commit()
    assert 0 <= world.seed < 2**63
    assert world.tick == 0
    assert world.created_at is not None


async def test_the_clock_moves_forward_only(db):
    world = World(name="Terra")
    db.add(world)
    await db.commit()
    assert await advance_clock(db, world.id) == 1
    assert await advance_clock(db, world.id, 1000) == 1001
    await db.commit()
    with pytest.raises(ValueError):
        await advance_clock(db, world.id, 0)


async def test_a_large_seed_survives_every_database(db):
    world = World(name="Terra", seed=2**63 - 1)
    db.add(world)
    await db.commit()
    db.expunge_all()
    assert (await db.scalar(select(World.seed))) == 2**63 - 1


async def test_fighters_have_a_place_on_a_map(db):
    world = World(name="Terra")
    db.add(world)
    await db.flush()
    hub = Map(world_id=world.id, name="hub", width=10, height=10)
    db.add(hub)
    await db.flush()
    db.add(Fighter(name="Jordie", map_id=hub.id, x=3, y=4))
    await db.commit()
    fighter = await db.scalar(select(Fighter))
    assert (fighter.map_id, fighter.x, fighter.y) == (hub.id, 3, 4)


async def test_map_names_are_unique_within_a_world(db):
    world = World(name="Terra")
    db.add(world)
    await db.flush()
    db.add_all([Map(world_id=world.id, name="hub"), Map(world_id=world.id, name="hub")])
    with pytest.raises(IntegrityError):
        await db.flush()


async def test_upsert_inserts_then_updates(db):
    table = World.__table__
    await upsert(db, table, {"name": "Terra", "seed": 1, "tick": 0}, key=["name"])
    await upsert(db, table, {"name": "Terra", "seed": 1, "tick": 5}, key=["name"])
    await db.commit()
    worlds = (await db.scalars(select(World))).all()
    assert [(w.name, w.tick) for w in worlds] == [("Terra", 5)]


async def test_upsert_with_nothing_to_change_leaves_the_row(db):
    table = World.__table__
    await upsert(db, table, {"name": "Terra", "seed": 1, "tick": 7}, key=["name"])
    await upsert(db, table, {"name": "Terra"}, key=["name"])
    await db.commit()
    assert (await db.scalar(select(World.tick))) == 7


async def test_text_matches_ignoring_case_on_every_database(db):
    db.add(World(name="Terra"))
    await db.commit()
    assert (await db.scalar(select(World.name).where(same_text(World.name, "TERRA")))) == "Terra"


async def test_accounts_log_in_with_any_case_of_their_name(db):
    await create_account(db, "Mike", "correct horse battery")
    await db.commit()
    assert (await authenticate(db, "mike", "correct horse battery")).username == "Mike"
    assert await authenticate(db, "Mike", "wrong horse battery") is None
    assert await authenticate(db, "nobody", "correct horse battery") is None


async def test_one_account_per_name_whatever_the_case(db):
    await create_account(db, "Mike", "correct horse battery")
    await db.commit()
    with pytest.raises(IntegrityError):
        await create_account(db, "MIKE", "another long password")


async def test_passwords_are_stored_as_argon2id(db):
    account = await create_account(db, "Mike", "correct horse battery")
    assert account.password_hash.startswith("$argon2id$")
    assert "correct horse battery" not in account.password_hash


@pytest.mark.parametrize(("username", "password"), [("ab", "long enough password"), ("bad name", "long enough password"), ("Mike", "short")])
async def test_account_rules(db, username, password):
    with pytest.raises(ValueError):
        await create_account(db, username, password)
    assert (await db.scalar(select(Account))) is None
