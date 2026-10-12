"""monster-party play: a seat counts missed rounds, says whether the AI plays now, and whether an admin sat in

Revision ID: 0042
Revises: 0041
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('monster_seats', schema=None) as batch_op:
        batch_op.add_column(sa.Column('missed', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('absent', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('admin', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('monster_seats', schema=None) as batch_op:
        batch_op.drop_column('admin')
        batch_op.drop_column('absent')
        batch_op.drop_column('missed')
