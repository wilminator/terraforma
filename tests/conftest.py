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


# --- the web app, on each database ----------------------------------------

SECRET = "x" * 32


@pytest.fixture
def mailbox():
    from terraforma.mail import MemoryMailer

    return MemoryMailer()


@pytest.fixture
def app_client(database_url, mailbox):
    """A TestClient for the engine app on a freshly migrated database (each database under test)."""
    from starlette.testclient import TestClient

    from terraforma.app import create_app
    from terraforma.settings import Settings

    async def prepare():
        engine = make_engine(database_url)
        await drop_everything(engine)
        await upgrade(engine)
        await engine.dispose()

    async def clean_up():
        engine = make_engine(database_url)
        await drop_everything(engine)
        await engine.dispose()

    run(prepare())
    settings = Settings(database_url=database_url, session_secret=SECRET, secure_cookies=False, public_url="http://game.test")
    with TestClient(create_app(settings, mailer=mailbox)) as client:
        yield client
    run(clean_up())


def in_app_db(client, work):
    """Runs async $work(session) against the app's own database, committed."""

    async def go():
        async with client.app.state.sessionmaker() as session:
            async with session.begin():
                return await work(session)

    return client.portal.call(go)
