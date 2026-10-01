"""The few things that differ between databases, and nowhere else.

Everything outside this module (and the migrations) is plain SQLAlchemy
that runs the same on Postgres, MySQL and SQLite.
"""

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import Table, func
from sqlalchemy.dialects import mysql, postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement


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
