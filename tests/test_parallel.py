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


def test_the_database_is_made_from_a_connection_that_doesnt_need_it(monkeypatch):
    """The worker's database doesn't exist yet, so the connection that makes it can't name it."""
    import terraforma.db.dialect as dialect

    seen = []

    class Connection:
        async def scalar(self, *args):
            return None

        async def execute(self, statement, *args):
            seen.append(str(statement))

    class Engine:
        def connect(self):
            return self

        async def __aenter__(self):
            return Connection()

        async def __aexit__(self, *exc):
            return False

        async def dispose(self):
            pass

    urls = []
    monkeypatch.setattr(dialect, "create_async_engine", lambda url, **kwargs: urls.append(url) or Engine())
    asyncio.run(dialect.ensure_database(MYSQL + "_gw1"))
    asyncio.run(dialect.ensure_database(POSTGRES + "_gw1"))
    assert [url.database for url in urls] == [None, "postgres"]
    assert seen == ["CREATE DATABASE IF NOT EXISTS `terraforma_test_gw1`", 'CREATE DATABASE "terraforma_test_gw1"']
