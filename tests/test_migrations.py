"""The migrations build exactly the tables the models describe, on every database, and undo cleanly."""

from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from terraforma import models  # noqa: F401
from terraforma.db.base import Base
from terraforma.db.migrate import alembic_config, downgrade
from terraforma.testing import forget_schema

pytestmark = pytest.mark.anyio


def test_the_migrations_form_one_chain_with_one_head():
    script = ScriptDirectory.from_config(alembic_config())
    revisions = [revision.revision for revision in script.walk_revisions()]
    assert len(revisions) == len(set(revisions)), "two migrations share a revision id"
    assert len(script.get_heads()) == 1, "two branches both extend the chain: re-point one's down_revision"


def test_the_migration_numbers_run_without_gaps_or_repeats_and_each_follows_the_one_before():
    """What a branch cut at the same time as another gets wrong: both take the next number, or one is left pointing at an old head."""
    script = ScriptDirectory.from_config(alembic_config())
    chain = sorted(script.walk_revisions(), key=lambda revision: revision.revision)
    numbers = [revision.revision for revision in chain]
    assert numbers == [f"{number:04d}" for number in range(1, len(chain) + 1)], f"numbers must be 0001, 0002, ... with none missing or shared; found {numbers}"
    for before, revision in zip([None, *chain], chain):
        assert revision.down_revision == (before.revision if before else None), (
            f"migration {revision.revision} must have down_revision {before.revision if before else None!r}: re-point it at the new head and rename it"
        )
        filename = Path(revision.path).name
        assert f"_{revision.revision}_" in filename, f"{filename} must carry its revision number {revision.revision}"


async def test_the_migrated_database_matches_the_models(engine):
    def differences(connection):
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        return compare_metadata(context, Base.metadata)

    async with engine.connect() as connection:
        assert await connection.run_sync(differences) == [], "a model changed without a migration (alembic revision --autogenerate)"


async def test_downgrading_to_the_start_removes_every_table(engine, database_url):
    forget_schema(database_url)  # the tables are about to go: the next test rebuilds them
    await downgrade(engine, "base")
    async with engine.connect() as connection:
        tables = await connection.run_sync(lambda sync: inspect(sync).get_table_names())
    assert set(tables) <= {"alembic_version"}


async def test_team_visibility_carries_over_what_was_listed_with_team_pages_on(engine, database_url):
    """0029: a team was seen when it was listed and its player had team pages on; that is what visible means now."""
    from datetime import UTC, datetime

    from sqlalchemy import Boolean, DateTime, Integer, MetaData, insert, select

    from terraforma.db.migrate import upgrade

    forget_schema(database_url)
    await downgrade(engine, "0028")

    def fill(connection):
        metadata = MetaData()
        metadata.reflect(connection)

        def row(table, **given):
            """A row with every column the table requires: a dummy for each the caller did not give."""
            values = dict(given)
            for column in metadata.tables[table].columns:
                if column.name not in values and not column.nullable and column.server_default is None:
                    kind = column.type
                    values[column.name] = datetime.now(UTC) if isinstance(kind, DateTime) else 1 if isinstance(kind, (Integer, Boolean)) else f"{table}-{column.name}"[:20]
            return values

        for number in (1, 2, 3):
            connection.execute(insert(metadata.tables["accounts"]).values(row("accounts", id=number, username=f"a{number}", username_key=f"a{number}")))
            connection.execute(insert(metadata.tables["teams"]).values(row("teams", id=number, account_id=number, name=f"t{number}", name_key=f"t{number}")))
        # Account 1 has pages on, 2 has them off, 3 never made a page; every team is listed except team 1's sibling case below.
        for number, pages in ((1, True), (2, False)):
            connection.execute(insert(metadata.tables["player_profiles"]).values(
                row("player_profiles", id=number, account_id=number, token=f"p{number}-token", team_pages=pages, team_alliances=False)))
        for number, listed in ((1, True), (2, True), (3, True)):
            connection.execute(insert(metadata.tables["team_profiles"]).values(row("team_profiles", id=number, team_id=number, token=f"t{number}-token", listed=listed)))

    async with engine.begin() as connection:
        await connection.run_sync(fill)
    await upgrade(engine)

    def visible(connection):
        metadata = MetaData()
        metadata.reflect(connection)
        table = metadata.tables["team_profiles"]
        return {team: shown for team, shown in connection.execute(select(table.c.team_id, table.c.visible)).all()}

    async with engine.connect() as connection:
        assert await connection.run_sync(visible) == {1: True, 2: False, 3: False}
