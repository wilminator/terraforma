"""Test fixtures for the engine and for games built on it (a pytest plugin).

In a game's tests/conftest.py:

    pytest_plugins = ["terraforma.testing"]

    @pytest.fixture
    def game():
        return MY_GAME          # app_client then serves your game

Every database test runs once per database. By default only SQLite runs.
To test Postgres and MySQL too, list their URLs, comma-separated, in
TERRAFORMA_TEST_DATABASES (the engine's compose.yml test service does this):

    TERRAFORMA_TEST_DATABASES="sqlite+aiosqlite:///{tmp}/test.db,postgresql+asyncpg://terraforma:terraforma@postgres/terraforma_test,mysql+aiomysql://terraforma:terraforma@mysql/terraforma_test"

``{tmp}`` becomes a fresh temporary folder per test. Under pytest-xdist
(``pytest -n auto``) each worker gets its own copy of every Postgres and
MySQL database, named like the listed one with the worker's id on the end
(``terraforma_test_gw0``), made on first use, so workers never drop each
other's tables. The MySQL user needs the right to make them (see
docker/mysql-test-init.sql). Postgres and MySQL
tests drop every table first and last, so point them at a test database.
"""

import asyncio
import os

import pytest

from sqlalchemy.engine import make_url

from terraforma.db.dialect import empty_tables, ensure_database
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


def per_worker(url: str, worker: str | None) -> str:
    """$url for this pytest-xdist $worker (its own database), or $url itself when not running in parallel or on SQLite."""
    parsed = make_url(url)
    if not worker or parsed.get_backend_name() == "sqlite":
        return url
    return parsed.set(database=f"{parsed.database}_{worker}").render_as_string(hide_password=False)


_made: set[str] = set()


@pytest.fixture(params=database_urls(), ids=database_id)
def database_url(request, tmp_path) -> str:
    url = per_worker(request.param.replace("{tmp}", str(tmp_path)), os.environ.get("PYTEST_XDIST_WORKER"))
    if url != request.param and url not in _made:
        asyncio.run(ensure_database(url))
        _made.add(url)
    return url


_migrated: set[str] = set()


async def fresh_database(engine, url: str) -> None:
    """Leaves $engine's database with every migration applied and no rows.

    Building the tables is the slow part on Postgres and MySQL, so each shared database is built once per
    run and then only emptied. A test that changes the tables must call forget_schema(url) when it is done.
    """
    if url in _migrated:
        await empty_tables(engine)
        return
    await drop_everything(engine)
    await upgrade(engine)
    if make_url(url).get_backend_name() != "sqlite":  # a SQLite file is made new for every test
        _migrated.add(url)


def forget_schema(url: str) -> None:
    """Says the database's tables are no longer the migrated ones, so the next test rebuilds them."""
    _migrated.discard(url)


@pytest.fixture
async def engine(database_url):
    """A database with every migration applied (and nothing else: it starts empty)."""
    engine = make_engine(database_url)
    await fresh_database(engine, database_url)
    yield engine
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
def game():
    """The game app_client serves: none for the engine's own tests; a game's conftest overrides it."""
    return None


@pytest.fixture
def app_client(database_url, mailbox, game, tmp_path):
    """A TestClient for the engine app on a freshly migrated database (each database under test), with its own key."""
    from starlette.testclient import TestClient

    from terraforma.app import create_app
    from terraforma.keys import new_key
    from terraforma.settings import Settings

    async def prepare():
        engine = make_engine(database_url)
        await fresh_database(engine, database_url)
        await engine.dispose()

    run(prepare())
    new_key(tmp_path / "keys")
    settings = Settings(
        database_url=database_url,
        session_secret=SECRET,
        secure_cookies=False,
        public_url="http://game.test",
        key_dir=tmp_path / "keys",
        fight_timer_seconds=0,  # tests resolve overdue rounds themselves, on the clock they move
        housekeeping_seconds=0,  # tests run the housekeeping pass themselves
    )
    with TestClient(create_app(settings, game, mailer=mailbox)) as client:
        yield client


def in_app_db(client, work):
    """Runs async $work(session) against the app's own database, committed."""

    async def go():
        async with client.app.state.sessionmaker() as session:
            async with session.begin():
                return await work(session)

    return client.portal.call(go)


def make_team(client, headers, name="Alpha", heroes=("Aria",), job="fighter"):
    """A team made over HTTP with its heroes, the one way a player makes either: the answer (the team's ``id``, ``name`` and ``members``)."""
    answer = client.post("/api/teams", json={"name": name, "heroes": [{"name": each, "job": job} for each in heroes]}, headers=headers)
    assert answer.status_code == 201, answer.text
    return answer.json()


def make_hero(client, headers, name="Aria", job="fighter"):
    """A hero made over HTTP, on a team of their own ("<name> team"): the hero as ``/api/heroes`` lists them."""
    make_team(client, headers, name=f"{name} team"[:24], heroes=(name,), job=job)
    return next(entry for entry in client.get("/api/heroes").json() if entry["name"] == name)
