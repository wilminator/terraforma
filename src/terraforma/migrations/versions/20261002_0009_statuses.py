"""statuses content table

Revision ID: 0009
Revises: 0008
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('statuses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('icon', sa.String(length=64), nullable=False),
    sa.Column('duration', sa.Integer(), nullable=True),
    sa.Column('intensity', sa.JSON(), nullable=False),
    sa.Column('ticks', sa.JSON(), nullable=False),
    sa.Column('modifiers', sa.JSON(), nullable=False),
    sa.Column('xp_share', sa.Float(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_statuses')),
    sa.UniqueConstraint('key', name=op.f('uq_statuses_key'))
    )


def downgrade() -> None:
    op.drop_table('statuses')
