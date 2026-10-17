"""Parallel test runs: each worker gets its own Postgres and MySQL database."""

import asyncio

import pytest

from terraforma.db.dialect import ensure_database
from terraforma.testing import per_worker

pytestmark = pytest.mark.platform

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


# --- the seeded copy of a SQLite database ------------------------------------------------------------------------------------


def rows_of(path):
    """Every table's rows in the SQLite file $path, as comparable data."""
    import sqlite3

    connection = sqlite3.connect(path)
    try:
        tables = [name for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        return {name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) for name in tables}
    finally:
        connection.close()


def test_an_app_started_from_the_seeded_copy_holds_what_a_fresh_start_loads(tmp_path, monkeypatch):
    """The first app for a game loads its seed; the next copies that database and skips loading, ending with the same rows."""
    from terraforma.example import GAME
    from terraforma.mail import MemoryMailer
    from terraforma.testing import serve

    monkeypatch.setattr("terraforma.testing._seeded", {})
    loads = []
    import terraforma.app as app_module

    real = app_module.load_content

    async def counting(*args, **kwargs):
        loads.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(app_module, "load_content", counting)
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    dumps = []
    for number in range(2):
        folder = tmp_path / f"start{number}"
        folder.mkdir()
        url = f"sqlite+aiosqlite:///{folder}/test.db"
        with serve(url, MemoryMailer(), GAME, [], folder, seeds, monkeypatch):
            pass
        dumps.append(rows_of(folder / "test.db"))
    assert len(loads) == 1, "only the first start loads the seed"
    assert dumps[0] == dumps[1]
    assert dumps[0]["items"], "and the seed was loaded"


def test_another_game_does_not_get_the_first_games_copy(tmp_path, monkeypatch):
    from terraforma.example import GAME
    from terraforma.mail import MemoryMailer
    from terraforma.testing import serve

    monkeypatch.setattr("terraforma.testing._seeded", {})
    seeds = tmp_path / "seeds"
    seeds.mkdir()
    for number, (game, addons) in enumerate([(GAME, []), (None, [])]):
        folder = tmp_path / f"start{number}"
        folder.mkdir()
        url = f"sqlite+aiosqlite:///{folder}/test.db"
        with serve(url, MemoryMailer(), game, addons, folder, seeds, monkeypatch):
            pass
    import terraforma.testing as testing

    assert len(testing._seeded) == 2, "a game with no seed has a copy of its own"


def make_game(folder, jobs):
    """A game whose seed holds just $jobs, built new each time, the way a test file's own ``game`` fixture does."""
    import json

    from terraforma.game import Game

    seed_dir = folder / "seed"
    seed_dir.mkdir(exist_ok=True)
    (seed_dir / "jobs.json").write_text(json.dumps(jobs))
    return Game(name="Test Game", seed_dir=seed_dir)


def test_a_game_built_again_from_the_same_seed_shares_the_copy_and_a_changed_seed_does_not(tmp_path, monkeypatch):
    """A test file's ``game`` fixture builds a new Game for every test: the copy is found by what the seed holds, not by the object."""
    import terraforma.app as app_module
    import terraforma.testing as testing
    from terraforma.mail import MemoryMailer

    monkeypatch.setattr(testing, "_seeded", {})
    loads = []
    real = app_module.load_content

    async def counting(*args, **kwargs):
        loads.append(1)
        return await real(*args, **kwargs)

    seeds = tmp_path / "seeds"
    seeds.mkdir()
    plain = [{"key": "fighter", "name": "Fighter"}]
    for number, jobs in enumerate([plain, plain, plain, [{"key": "fighter", "name": "Warrior"}]]):
        folder = tmp_path / f"start{number}"
        (folder / "game").mkdir(parents=True)
        url = f"sqlite+aiosqlite:///{folder}/test.db"
        with pytest.MonkeyPatch.context() as each:  # (a copied start leaves load_content replaced until its monkeypatch is undone)
            each.setattr(app_module, "load_content", counting)
            with testing.serve(url, MemoryMailer(), make_game(folder / "game", jobs), [], folder, seeds, each):
                pass
    assert len(loads) == 2, "the three identical games load once, the changed seed loads again"
    assert len(testing._seeded) == 2


def test_the_fingerprint_follows_the_seed_the_rules_and_the_add_ons(tmp_path):
    from dataclasses import replace

    from terraforma.fights.rules import Rules as StandardRules
    from terraforma.testing import seed_fingerprint

    class Rarer(StandardRules):
        drop_chance_scale = 100

    jobs = [{"key": "fighter", "name": "Fighter"}]
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    first, second = make_game(tmp_path / "a", jobs), make_game(tmp_path / "b", jobs)
    assert seed_fingerprint(first, []) == seed_fingerprint(second, []), "the same seed in another folder"
    assert seed_fingerprint(first, []) != seed_fingerprint(None, [])
    assert seed_fingerprint(first, []) != seed_fingerprint(replace(second, rules=Rarer()), [])
    (tmp_path / "b" / "seed" / "abilities.json").write_text("[]")
    assert seed_fingerprint(first, []) != seed_fingerprint(second, []), "a file added to the seed"


def test_a_blank_sqlite_database_is_built_once_and_copied(tmp_path, monkeypatch):
    """The tables of a new SQLite file come from a copy of ones built earlier, empty and stamped like a built database."""
    import terraforma.testing as testing
    from terraforma.db import schema
    from terraforma.db.session import make_engine

    monkeypatch.setattr(testing, "_blank", {})
    builds = []
    real = testing.ensure_schema

    async def counting(engine):
        builds.append(1)
        await real(engine)

    monkeypatch.setattr(testing, "ensure_schema", counting)

    async def start(number):
        url = f"sqlite+aiosqlite:///{tmp_path}/blank{number}.db"
        engine = make_engine(url)
        await testing.fresh_database(engine, url)
        await schema.ensure_schema(engine)  # what the app does next: a copy must pass as the models' own tables
        await engine.dispose()
        return rows_of(tmp_path / f"blank{number}.db")

    first, second = asyncio.run(start(0)), asyncio.run(start(1))
    assert len(builds) == 1, "only the first database is built"
    assert first == second
    assert len(first) > 50 and not any(rows for name, rows in first.items() if name != schema.STAMP_TABLE)
