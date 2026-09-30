"""Bringing a database's tables up to date (Alembic, run by the engine itself).

    python -m terraforma migrate          # uses settings.toml

Making a new migration after changing the models (developers):

    alembic revision --autogenerate -m "what changed"

then read it over: autogenerate misses renames and some type changes.
"""

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData
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


async def drop_everything(engine: AsyncEngine) -> None:
    """Every table in the database, whoever made it (tests only: start from nothing)."""

    def drop(connection) -> None:
        metadata = MetaData()
        metadata.reflect(connection)
        metadata.drop_all(connection)

    async with engine.begin() as connection:
        await connection.run_sync(drop)
