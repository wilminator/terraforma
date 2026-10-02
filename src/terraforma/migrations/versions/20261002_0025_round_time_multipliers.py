"""player round time settings

Revision ID: 0025
Revises: 0024
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0025'
down_revision: str | None = '0024'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('player_settings',
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('time_multiplier', sa.Float(), server_default='1.0', nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], ),
    sa.PrimaryKeyConstraint('account_id')
    )
    op.add_column('fights', sa.Column('time_multiplier', sa.Float(), server_default='1.0', nullable=False))


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    op.drop_column('fights', 'time_multiplier')
    op.drop_table('player_settings')
