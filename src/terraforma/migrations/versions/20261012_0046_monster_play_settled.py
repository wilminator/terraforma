"""monster-party play: whether a seat was settled when its fight ended

Revision ID: 0046
Revises: 0045
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0046'
down_revision: str | None = '0045'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('monster_seats', schema=None) as batch_op:
        batch_op.add_column(sa.Column('settled', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('monster_seats', schema=None) as batch_op:
        batch_op.drop_column('settled')
