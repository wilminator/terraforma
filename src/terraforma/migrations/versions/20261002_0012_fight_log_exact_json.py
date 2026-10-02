"""store a fight's snapshots, commands and events as exact text

MySQL's JSON type re-formats floats (0.11666666666666667 reads back as 0.11666666666666668), which broke the hash
chain and would let a replay differ in the last bit. The fight log is not queried by key, so it is kept as exact
JSON text instead (terraforma.db.dialect.ExactJSON). Rows already written keep their old text.

Revision ID: 0012
Revises: 0008
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from terraforma.db.dialect import ExactJSON


revision: str = '0012'
down_revision: str | None = '0011'  # the last migration before it: team gold (#25)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = (
    ('fights', 'initial_state', False),
    ('fight_actions', 'commands', False),
    ('fight_actions', 'events', False),
    ('fight_actions', 'final_state', True),
)


def upgrade() -> None:
    for table, column, nullable in COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                column, existing_type=sa.JSON(), type_=ExactJSON(), existing_nullable=nullable,
                postgresql_using=f'{column}::text',
            )


def downgrade() -> None:
    for table, column, nullable in COLUMNS:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                column, existing_type=ExactJSON(), type_=sa.JSON(), existing_nullable=nullable,
                postgresql_using=f'{column}::json',
            )
