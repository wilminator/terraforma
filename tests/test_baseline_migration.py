"""Until the first release there are no migration files, but the one the release will start from must always work.

The baseline is what ``alembic revision --autogenerate`` writes for an empty database: this test generates it in
memory, runs it on each database, and checks the result is exactly the tables the models describe, and that undoing
it removes them. Nothing is committed, so no two branches can collide on it.
"""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata, produce_migrations
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from terraforma import models  # noqa: F401
from terraforma.db.base import Base
from terraforma.db.migrate import alembic_config
from terraforma.db.schema import drop_everything
from terraforma.testing import forget_schema

pytestmark = pytest.mark.anyio


def test_there_are_no_migration_files_yet():
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    assert list(script.walk_revisions()) == [], "no migrations until the first release: the models are the schema (see db/schema.py)"
    assert not [path for path in Path(script.versions).iterdir() if path.suffix == ".py"]


async def test_the_generated_baseline_builds_the_models_and_undoes_cleanly(engine, database_url):
    forget_schema(database_url)  # this test makes its own tables
    await drop_everything(engine)

    def build_and_undo(connection) -> None:
        context = MigrationContext.configure(connection, opts={"compare_type": True, "target_metadata": Base.metadata})
        script = produce_migrations(context, Base.metadata)
        operations = Operations(context)

        def run(steps) -> None:
            for step in steps:
                if hasattr(step, "ops"):  # a group of changes to one table (its indexes)
                    run(step.ops)
                else:
                    operations.invoke(step)

        run(script.upgrade_ops.ops)
        assert compare_metadata(context, Base.metadata) == [], "the baseline differs from the models"
        run(script.downgrade_ops.ops)
        assert inspect(connection).get_table_names() == []

    async with engine.begin() as connection:
        await connection.run_sync(build_and_undo)
