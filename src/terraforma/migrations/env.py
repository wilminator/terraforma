"""How Alembic reaches the database.

The engine runs migrations itself (terraforma.db.migrate), handing over
an open connection. Run from the command line (``alembic``), the URL
comes from the settings file instead.
"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from terraforma import models  # noqa: F401  (registers every table)
from terraforma.db.base import Base

config = context.config
target_metadata = Base.metadata


def configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        # SQLite can't ALTER most things: rebuild the table instead.
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_with_own_engine() -> None:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        from terraforma.settings import load_settings

        url = load_settings().database_url
    engine = async_engine_from_config({"sqlalchemy.url": url}, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(configure)
    await engine.dispose()


connection = config.attributes.get("connection")
if connection is not None:
    configure(connection)
elif context.is_offline_mode():
    raise SystemExit("Offline (SQL script) migrations aren't supported: run them against a database.")
else:
    asyncio.run(run_with_own_engine())
