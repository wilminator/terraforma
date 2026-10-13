"""Alembic, held ready for the first release.

Until then there are no migration files: the tables are built from the models (terraforma.db.schema)
and a development database is reset when the models change. At the first release the baseline
migration ``0001`` is generated from the models (``alembic revision --autogenerate -m baseline``),
``serve`` goes back to running ``upgrade``, and every change after that is a numbered migration.
tests/test_baseline_migration.py keeps this path working in the meantime.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy.ext.asyncio import AsyncEngine


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", "terraforma:migrations")
    return config


def _run(connection, action: str, revision: str) -> None:
    config = alembic_config()
    config.attributes["connection"] = connection
    getattr(command, action)(config, revision)


async def upgrade(engine: AsyncEngine, revision: str = "head") -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_run, "upgrade", revision)


async def downgrade(engine: AsyncEngine, revision: str = "base") -> None:
    async with engine.begin() as connection:
        await connection.run_sync(_run, "downgrade", revision)
