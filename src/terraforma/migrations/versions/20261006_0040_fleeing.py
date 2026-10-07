"""fleeing: which fighters have left a fight, and whether a fled hero's share of the result was added

Revision ID: 0040
Revises: 0039
Created: 2026-10-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0040'
down_revision: str | None = '0039'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('fight_participants', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fled', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('settled', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('fight_participants', schema=None) as batch_op:
        batch_op.drop_column('settled')
        batch_op.drop_column('fled')
