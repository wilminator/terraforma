"""The database's tables, built straight from the models until the first release.

There are no migration files yet (see terraforma.db.migrate): nothing is live, so a table
change is just a model change. The tables are made when the database is empty, and a
one-row stamp remembers which models made them. A database made by other models is
refused with a clear message instead of failing later on a missing column; the developer
resets it (``python -m terraforma reset-database --yes``).
"""

import hashlib

from sqlalchemy import Column, MetaData, String, Table, inspect
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.schema import CreateIndex, CreateTable

from terraforma import models  # noqa: F401  (registers every table)
from terraforma.db.base import Base

STAMP_TABLE = "schema_stamp"
_stamp_metadata = MetaData()
_stamp = Table(STAMP_TABLE, _stamp_metadata, Column("hash", String(64), primary_key=True))


class SchemaOutdated(RuntimeError):
    """The database was made by different models than the ones running."""


def schema_hash() -> str:
    """A fingerprint of every table, column, constraint and index the models describe (the same on every database)."""
    dialect = sqlite.dialect()
    statements = []
    for table in Base.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect)))
        statements.extend(str(CreateIndex(index).compile(dialect=dialect)) for index in sorted(table.indexes, key=lambda index: str(index.name)))
    return hashlib.sha256("\n".join(statements).encode()).hexdigest()


async def ensure_schema(engine: AsyncEngine) -> None:
    """Makes the tables in an empty database; leaves one made by these models alone; refuses any other."""

    def work(connection) -> None:
        tables = set(inspect(connection).get_table_names())
        if not tables:
            Base.metadata.create_all(connection)
            _stamp.create(connection)
            connection.execute(_stamp.insert().values(hash=schema_hash()))
            return
        stamped = connection.execute(_stamp.select()).scalar() if STAMP_TABLE in tables else None
        if stamped != schema_hash():
            raise SchemaOutdated(
                "The database's tables were made by different models than this version of TerraForma. Until the first release there are no "
                "migrations: reset the development database with `python -m terraforma reset-database --yes` (this deletes everything in it)."
            )

    async with engine.begin() as connection:
        await connection.run_sync(work)


async def drop_everything(engine: AsyncEngine) -> None:
    """Every table in the database, whoever made it (tests and reset-database: start from nothing)."""

    def drop(connection) -> None:
        metadata = MetaData()
        metadata.reflect(connection)
        metadata.drop_all(connection)

    async with engine.begin() as connection:
        await connection.run_sync(drop)


async def reset_schema(engine: AsyncEngine) -> None:
    """Deletes every table and makes them again from the models (development only)."""
    await drop_everything(engine)
    await ensure_schema(engine)
