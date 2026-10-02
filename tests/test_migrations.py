"""The migrations build exactly the tables the models describe, on every database, and undo cleanly."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from terraforma import models  # noqa: F401
from terraforma.db.base import Base
from terraforma.db.migrate import alembic_config, downgrade
from terraforma.testing import forget_schema

pytestmark = pytest.mark.anyio


def test_the_migrations_form_one_chain_with_one_head():
    script = ScriptDirectory.from_config(alembic_config())
    revisions = [revision.revision for revision in script.walk_revisions()]
    assert len(revisions) == len(set(revisions)), "two migrations share a revision id"
    assert len(script.get_heads()) == 1, "two branches both extend the chain: re-point one's down_revision"


async def test_the_migrated_database_matches_the_models(engine):
    def differences(connection):
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == [], "a model changed without a migration (alembic revision --autogenerate)"


async def test_downgrading_to_the_start_removes_every_table(engine, database_url):
    forget_schema(database_url)  # the tables are about to go: the next test rebuilds them
    await downgrade(engine, "base")
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert set(tables) <= {"alembic_version"}
