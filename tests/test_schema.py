"""The tables are built from the models; a database made by other models is refused, not half-used."""

import pytest
from sqlalchemy import inspect, text

from terraforma.db.base import Base
from terraforma.db.schema import STAMP_TABLE, SchemaOutdated, ensure_schema, reset_schema, schema_hash
from terraforma.testing import forget_schema

pytestmark = pytest.mark.anyio


async def tables(engine) -> set[str]:
    async with engine.connect() as connection:
        return set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))


async def test_an_empty_database_gets_every_model_table_and_the_stamp(engine):
    assert await tables(engine) == {table.name for table in Base.metadata.tables.values()} | {STAMP_TABLE}
    async with engine.connect() as connection:
        assert (await connection.execute(text(f"SELECT hash FROM {STAMP_TABLE}"))).scalar() == schema_hash()


async def test_running_it_again_changes_nothing(engine):
    await ensure_schema(engine)
    await ensure_schema(engine)
    async with engine.connect() as connection:
        assert (await connection.execute(text(f"SELECT count(*) FROM {STAMP_TABLE}"))).scalar() == 1


async def test_a_database_made_by_other_models_is_refused_with_the_way_out(engine, database_url):
    forget_schema(database_url)  # the stamp is about to change: the next test rebuilds the tables
    async with engine.begin() as connection:
        await connection.execute(text(f"UPDATE {STAMP_TABLE} SET hash = 'older models'"))
    with pytest.raises(SchemaOutdated, match="reset-database"):
        await ensure_schema(engine)


async def test_tables_with_no_stamp_are_refused_too(engine, database_url):
    forget_schema(database_url)
    async with engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE {STAMP_TABLE}"))
    with pytest.raises(SchemaOutdated):
        await ensure_schema(engine)


async def test_reset_makes_a_stale_database_current_again(engine, database_url):
    forget_schema(database_url)
    async with engine.begin() as connection:
        await connection.execute(text(f"UPDATE {STAMP_TABLE} SET hash = 'older models'"))
    await reset_schema(engine)
    await ensure_schema(engine)
    async with engine.connect() as connection:
        assert (await connection.execute(text(f"SELECT hash FROM {STAMP_TABLE}"))).scalar() == schema_hash()


def test_the_fingerprint_follows_the_models_and_nothing_else():
    assert schema_hash() == schema_hash()
    assert len(schema_hash()) == 64
