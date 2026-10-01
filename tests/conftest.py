"""Test setup: every database test runs once per database.

By default only SQLite runs. To test Postgres and MySQL too, list their
URLs, comma-separated, in TERRAFORMA_TEST_DATABASES (compose.yml's test
service does this):

    TERRAFORMA_TEST_DATABASES="sqlite+aiosqlite:///{tmp}/test.db,postgresql+asyncpg://terraforma:terraforma@postgres/terraforma_test,mysql+aiomysql://terraforma:terraforma@mysql/terraforma_test"

``{tmp}`` becomes a fresh temporary folder per test. Postgres and MySQL
tests drop every table first and last, so point them at a test database.
"""

import asyncio
import os

import pytest

from terraforma.db.migrate import drop_everything, upgrade
from terraforma.db.session import make_engine, make_sessionmaker

DEFAULT_DATABASES = "sqlite+aiosqlite:///{tmp}/test.db"


def database_urls() -> list[str]:
    return [url.strip() for url in os.environ.get("TERRAFORMA_TEST_DATABASES", DEFAULT_DATABASES).split(",") if url.strip()]


def database_id(url: str) -> str:
    return url.split("+", 1)[0].split(":", 1)[0]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(params=database_urls(), ids=database_id)
def database_url(request, tmp_path) -> str:
    return request.param.replace("{tmp}", str(tmp_path))


@pytest.fixture
async def engine(database_url):
    """A database with every migration applied (and nothing else: it starts empty)."""
    engine = make_engine(database_url)
    await drop_everything(engine)
    await upgrade(engine)
    yield engine
    await drop_everything(engine)
    await engine.dispose()


@pytest.fixture
async def db(engine):
    """A session on that database."""
    async with make_sessionmaker(engine)() as session:
        yield session


def run(coroutine):
    """Runs async setup from a plain (sync) test, such as one using TestClient."""
    return asyncio.run(coroutine)
