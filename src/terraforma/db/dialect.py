"""The few things that differ between databases, and nowhere else.

Everything outside this module (and the migrations) is plain SQLAlchemy
that runs the same on Postgres, MySQL and SQLite.
"""

import json
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import MetaData, Table, Text, func, text
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.types import TypeDecorator

from .base import canonical_json


async def upsert(
    session: AsyncSession,
    table: Table,
    values: Mapping[str, Any],
    key: Iterable[str],
) -> None:
    """Inserts $values, or updates the row whose $key columns match."""
    key = list(key)
    changes = {name: value for name, value in values.items() if name not in key}
    name = session.bind.dialect.name
    if name == "postgresql":
        statement = postgresql.insert(table).values(**values)
        statement = (
            statement.on_conflict_do_update(index_elements=key, set_=changes)
            if changes
            else statement.on_conflict_do_nothing(index_elements=key)
        )
    elif name == "sqlite":
        statement = sqlite.insert(table).values(**values)
        statement = (
            statement.on_conflict_do_update(index_elements=key, set_=changes)
            if changes
            else statement.on_conflict_do_nothing(index_elements=key)
        )
    elif name in ("mysql", "mariadb"):
        statement = mysql.insert(table).values(**values)
        # MySQL has no "do nothing": setting a key column to itself is the idiom.
        statement = statement.on_duplicate_key_update(**(changes or {key[0]: statement.inserted[key[0]]}))
    else:
        raise NotImplementedError(f"upsert() doesn't know the {name} database yet")
    await session.execute(statement)


async def increment(session: AsyncSession, table: Table, key: Mapping[str, Any], column: str) -> None:
    """Adds 1 to $column of the row with $key, making the row (at 1) if there isn't one. Safe under concurrent calls."""
    name = session.bind.dialect.name
    values = {**key, column: 1}
    if name in ("postgresql", "sqlite"):
        module = postgresql if name == "postgresql" else sqlite
        statement = module.insert(table).values(**values)
        statement = statement.on_conflict_do_update(index_elements=list(key), set_={column: table.c[column] + 1})
    elif name in ("mysql", "mariadb"):
        statement = mysql.insert(table).values(**values)
        statement = statement.on_duplicate_key_update({column: table.c[column] + 1})
    else:
        raise NotImplementedError(f"increment() doesn't know the {name} database yet")
    await session.execute(statement)


def same_text(column: ColumnElement[str], value: str) -> ColumnElement[bool]:
    """Case-insensitive equality: MySQL's default collation already ignores case, Postgres and SQLite don't."""
    return func.lower(column) == value.lower()


async def ensure_database(url: str) -> None:
    """Makes the database $url names, if the server doesn't have it yet (tests: one database per parallel worker).

    SQLite makes its file on first use, so there is nothing to do for it.
    """
    parsed = make_url(url)
    name = parsed.database
    backend = parsed.get_backend_name()
    if backend == "sqlite":
        return
    if not name or not name.replace("_", "").isalnum():
        raise ValueError(f"won't make a database called {name!r}")
    # The database being made can't be the one connected to: MySQL connects to
    # none, Postgres to its always-present maintenance database.
    # (URL.set(database=None) would leave the name in place, so _replace it.)
    server = parsed._replace(database="postgres" if backend == "postgresql" else None)
    engine = create_async_engine(server, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            if backend == "postgresql":
                exists = await connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name})
                if not exists:
                    await connection.execute(text(f'CREATE DATABASE "{name}"'))
            elif backend in ("mysql", "mariadb"):
                await connection.execute(text(f"CREATE DATABASE IF NOT EXISTS `{name}`"))
            else:
                raise NotImplementedError(f"ensure_database() doesn't know the {backend} database yet")
    finally:
        await engine.dispose()


async def empty_tables(engine: AsyncEngine) -> None:
    """Deletes every row of every table but Alembic's, and restarts the id counters (tests: much faster than rebuilding the tables)."""

    def empty(connection) -> None:
        metadata = MetaData()
        metadata.reflect(connection)
        names = [table.name for table in metadata.sorted_tables if table.name != "alembic_version"]
        if not names:
            return
        name = connection.dialect.name
        if name == "postgresql":
            quoted = ", ".join(f'"{table}"' for table in names)
            connection.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))
        elif name in ("mysql", "mariadb"):
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
            for table in names:
                connection.execute(text(f"TRUNCATE TABLE `{table}`"))
            connection.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        elif name == "sqlite":
            for table in reversed(metadata.sorted_tables):
                if table.name != "alembic_version":
                    connection.execute(table.delete())
        else:
            raise NotImplementedError(f"empty_tables() doesn't know the {name} database yet")

    async with engine.begin() as connection:
        await connection.run_sync(empty)


class ExactJSON(TypeDecorator):
    """JSON kept as its exact text, for data that must read back bit for bit (a hash covers it, or a replay depends on it).

    The databases' own JSON types re-format what they store: MySQL, for one, turns the float 0.11666666666666667
    into 0.11666666666666668. This stores canonical JSON text in a text column (LONGTEXT on MySQL, which would
    otherwise cut it at 64 KB), so every database returns exactly what was written. Not queryable by key.
    """

    impl = Text
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name in ("mysql", "mariadb"):
            return dialect.type_descriptor(mysql.LONGTEXT())
        return dialect.type_descriptor(Text())

    def process_bind_param(self, value, dialect):
        return None if value is None else canonical_json(value)

    def process_result_value(self, value, dialect):
        return None if value is None else json.loads(value)
