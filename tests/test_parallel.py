"""Parallel test runs: each worker gets its own Postgres and MySQL database."""

import asyncio

from terraforma.db.dialect import ensure_database
from terraforma.testing import per_worker

POSTGRES = "postgresql+asyncpg://terraforma:p%40ss@postgres-test/terraforma_test"
MYSQL = "mysql+aiomysql://terraforma:terraforma@mysql-test/terraforma_test"


def test_a_worker_gets_its_own_database_name():
    assert per_worker(POSTGRES, "gw3") == "postgresql+asyncpg://terraforma:p%40ss@postgres-test/terraforma_test_gw3"
    assert per_worker(MYSQL, "gw0") == "mysql+aiomysql://terraforma:terraforma@mysql-test/terraforma_test_gw0"


def test_without_workers_or_on_sqlite_the_url_is_left_alone():
    assert per_worker(POSTGRES, None) == POSTGRES
    sqlite = "sqlite+aiosqlite:////tmp/test.db"
    assert per_worker(sqlite, "gw1") == sqlite


def test_sqlite_needs_no_database_made():
    asyncio.run(ensure_database("sqlite+aiosqlite:////tmp/never-made.db"))
