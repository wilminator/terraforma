"""personality art: the picture of a personality in a fight and on the map

Revision ID: 0041
Revises: 0040
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0041'
down_revision: str | None = '0040'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('personalities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fight_art', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('map_art', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('personalities', schema=None) as batch_op:
        batch_op.drop_column('map_art')
        batch_op.drop_column('fight_art')
