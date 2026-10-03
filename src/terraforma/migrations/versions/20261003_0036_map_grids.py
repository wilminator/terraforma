"""maps carry a tile grid, a zone grid, wrap flags for each direction and a revision

Revision ID: 0036
Revises: 0035
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0036'
down_revision: str | None = '0035'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('maps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=64), server_default='', nullable=False))
        batch_op.add_column(sa.Column('wrap_x', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('wrap_y', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.add_column(sa.Column('safe_steps', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('revision', sa.Integer(), server_default='1', nullable=False))
        batch_op.add_column(sa.Column('tileset', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('tiles', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('zones', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('zone_tiles', sa.JSON(), nullable=True))
        batch_op.drop_column('wraps')


def downgrade() -> None:
    with op.batch_alter_table('maps', schema=None) as batch_op:
        batch_op.add_column(sa.Column('wraps', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.drop_column('zone_tiles')
        batch_op.drop_column('zones')
        batch_op.drop_column('tiles')
        batch_op.drop_column('tileset')
        batch_op.drop_column('revision')
        batch_op.drop_column('safe_steps')
        batch_op.drop_column('wrap_y')
        batch_op.drop_column('wrap_x')
        batch_op.drop_column('title')
