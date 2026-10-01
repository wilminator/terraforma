"""The migrations build exactly the tables the models describe, on every database, and undo cleanly."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from terraforma import models  # noqa: F401
from terraforma.db.base import Base
from terraforma.db.migrate import downgrade

pytestmark = pytest.mark.anyio


async def test_the_migrated_database_matches_the_models(engine):
    def differences(connection):
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == [], "a model changed without a migration (alembic revision --autogenerate)"


async def test_downgrading_to_the_start_removes_every_table(engine):
    await downgrade(engine, "base")
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert set(tables) <= {"alembic_version"}
