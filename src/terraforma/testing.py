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

On SQLite, ``app_client`` builds the tables and loads the game's seed once per worker for each distinct seed it serves, and starts every
later test from a copy of that database (``serve``). So a test sees what a fresh start loads, a few seconds sooner.
"""

import asyncio
import atexit
import hashlib
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from sqlalchemy.engine import make_url

from terraforma.addons import integrate
from terraforma.db.dialect import empty_tables, ensure_database
from terraforma.db.schema import drop_everything, ensure_schema, schema_hash
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


_built: set[str] = set()

#: SQLite files with the tables made and no rows, by the models' ``schema_hash``: making the ~100 tables takes most of a second, and a SQLite
#: file is new for every test, so the first test of a worker builds them and later tests start from a copy.
_blank: dict[str, Path] = {}
_blank_folder: Path | None = None


def blank_folder() -> Path:
    """Where the worker keeps its blank SQLite databases (made when first needed, removed when the worker exits)."""
    global _blank_folder
    if _blank_folder is None:
        _blank_folder = Path(tempfile.mkdtemp(prefix="terraforma-blank-"))
        atexit.register(shutil.rmtree, _blank_folder, ignore_errors=True)
    return _blank_folder


async def fresh_database(engine, url: str) -> None:
    """Leaves $engine's database with every table made from the models and no rows.

    Building the tables is the slow part, so each shared Postgres or MySQL database is built once per run and then only
    emptied, and a SQLite file (new for every test) is copied from one the worker built earlier. A test that changes the
    tables must call forget_schema(url) when it is done. The engine must not have connected yet (a SQLite file is replaced).
    """
    if url in _built:
        await empty_tables(engine)
        return
    path = sqlite_file(url)
    blank = _blank.get(schema_hash()) if path else None
    if blank:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(blank, path)
        return
    await drop_everything(engine)
    await ensure_schema(engine)
    if path:
        if len(_blank) < SEEDED_LIMIT:
            target = blank_folder() / f"blank-{len(_blank)}.db"
            snapshot(path, target)
            _blank[schema_hash()] = target
    else:
        _built.add(url)


def forget_schema(url: str) -> None:
    """Says the database's tables are no longer the built ones, so the next test rebuilds them."""
    _built.discard(url)
    _blank.clear()


@pytest.fixture
async def engine(database_url):
    """A database with every table made from the models (and nothing else: it starts empty)."""
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
def addons():
    """The add-ons app_client turns on (the settings' ``addons``): none, unless a test or a game's conftest says."""
    return []


#: Seeded SQLite databases, one per seed a worker has served: starting an app builds the tables and loads the game's whole seed,
#: which costs about two seconds, and every test used to pay it. The first test to serve a seed keeps a copy of its database as it
#: was just after the app started; later tests start from a copy of that file and skip both steps.
#: A seed is known by what it holds (``seed_fingerprint``), not by the Game object: a test file whose ``game`` fixture builds the
#: same game for every test still shares one copy. The number of entries is capped (a test that makes a seed of its own gets an
#: entry of its own).
_seeded: dict[str, Path] = {}
SEEDED_LIMIT = 8


def seed_fingerprint(game, addons) -> str:
    """What the app loads into an empty database for $game with $addons: the bytes of every seed file (and where each sits), the
    add-ons listed, and the rules that shape the stored rows. Two games with the same fingerprint start from the same database."""
    digest = hashlib.sha256()
    integrated = integrate(game, addons)
    for addon, folder in integrated.seed_dirs:
        digest.update(f"[{addon}]".encode())
        for path in sorted(Path(folder).rglob("*.json")):
            digest.update(path.relative_to(folder).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    if game:
        digest.update(repr((game.rules.stats, game.rules.resource_names, game.rules.drop_chance_scale)).encode())
        digest.update(repr([(type(each).__module__, type(each).__qualname__, each.name) for each in game.state_sections]).encode())
    digest.update(repr(tuple(addons)).encode())
    return digest.hexdigest()


def sqlite_file(url: str) -> Path | None:
    """The file a SQLite $url names, or None for another database (or an in-memory one)."""
    parsed = make_url(url)
    return Path(parsed.database) if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:") else None


def snapshot(source: Path, target: Path) -> None:
    """Copies the SQLite database $source to $target, safe while the app has it open."""
    origin = sqlite3.connect(source)
    try:
        copy = sqlite3.connect(target)
        try:
            origin.backup(copy)
        finally:
            copy.close()
    finally:
        origin.close()


async def _already_loaded(*args, **kwargs) -> dict[str, int]:
    """Stands in for load_content when the database was copied from one that has the game's seed loaded."""
    return {}


@contextmanager
def serve(database_url, mailbox, game, addons, folder, seeds, monkeypatch):
    """A TestClient for the engine app on a database holding the game's seed (each database under test), with its own key in $folder.

    $seeds is where the seeded copies of SQLite databases are kept (a folder that lives as long as the worker)."""
    from starlette.testclient import TestClient

    from terraforma.app import create_app
    from terraforma.keys import new_key
    from terraforma.settings import Settings

    path = sqlite_file(database_url)
    key = seed_fingerprint(game, addons) if path else None
    seeded = _seeded.get(key) if path else None
    if seeded:
        shutil.copyfile(seeded, path)
        monkeypatch.setattr("terraforma.app.load_content", _already_loaded)  # the copy has what it would load
    else:

        async def prepare():
            engine = make_engine(database_url)
            await fresh_database(engine, database_url)
            await engine.dispose()

        run(prepare())
    new_key(folder / "keys")
    settings = Settings(
        database_url=database_url,
        session_secret=SECRET,
        secure_cookies=False,
        public_url="http://game.test",
        addons=addons,
        key_dir=folder / "keys",
        fight_timer_seconds=0,  # tests resolve overdue rounds themselves, on the clock they move
        housekeeping_seconds=0,  # tests run the housekeeping pass themselves
    )
    with TestClient(create_app(settings, game, mailer=mailbox)) as client:
        if path and not seeded and len(_seeded) < SEEDED_LIMIT:
            target = seeds / f"seeded-{len(_seeded)}.db"
            snapshot(path, target)
            _seeded[key] = target
        yield client


@pytest.fixture
def app_client(database_url, mailbox, game, addons, tmp_path, tmp_path_factory, monkeypatch):
    """A TestClient for the engine app on a freshly built database (each database under test), with its own key."""
    seeds = tmp_path_factory.getbasetemp() / "seeded"  # (under pytest-xdist the base folder is the worker's own)
    seeds.mkdir(exist_ok=True)
    with serve(database_url, mailbox, game, addons, tmp_path, seeds, monkeypatch) as client:
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
