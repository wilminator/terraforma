"""content tables: abilities, items, jobs, personalities, monsters

Revision ID: 0005
Revises: 0004
Created: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _row_columns() -> list[sa.Column]:
    return [
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table('abilities',
    *_row_columns(),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('mp_cost', sa.Integer(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('icon', sa.String(length=64), nullable=False),
    sa.Column('effect', sa.JSON(), nullable=False),
    sa.Column('presentation', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_abilities')),
    sa.UniqueConstraint('key', name=op.f('uq_abilities_key'))
    )
    op.create_table('items',
    *_row_columns(),
    sa.Column('price', sa.Integer(), nullable=False),
    sa.Column('one_use', sa.Boolean(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=False),
    sa.Column('icon', sa.String(length=64), nullable=False),
    sa.Column('use_effect', sa.JSON(), nullable=True),
    sa.Column('equip_slots', sa.JSON(), nullable=True),
    sa.Column('stat_bonus', sa.JSON(), nullable=False),
    sa.Column('stat_percent', sa.JSON(), nullable=False),
    sa.Column('attack', sa.JSON(), nullable=True),
    sa.Column('use_presentation', sa.JSON(), nullable=False),
    sa.Column('fight_presentation', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_items')),
    sa.UniqueConstraint('key', name=op.f('uq_items_key'))
    )
    op.create_table('jobs',
    *_row_columns(),
    sa.Column('xp_needed', sa.Integer(), nullable=False),
    sa.Column('stat_growth', sa.JSON(), nullable=False),
    sa.Column('abilities', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    sa.UniqueConstraint('key', name=op.f('uq_jobs_key'))
    )
    op.create_table('personalities',
    *_row_columns(),
    sa.Column('animations', sa.JSON(), nullable=False),
    sa.Column('overworld', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_personalities')),
    sa.UniqueConstraint('key', name=op.f('uq_personalities_key'))
    )
    op.create_table('monsters',
    *_row_columns(),
    sa.Column('personality', sa.String(length=64), nullable=False),
    sa.Column('xp_reward', sa.Integer(), nullable=False),
    sa.Column('gold_reward', sa.Integer(), nullable=False),
    sa.Column('stats', sa.JSON(), nullable=False),
    sa.Column('abilities', sa.JSON(), nullable=False),
    sa.Column('items', sa.JSON(), nullable=False),
    sa.Column('equipment', sa.JSON(), nullable=False),
    sa.Column('ai', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_monsters')),
    sa.UniqueConstraint('key', name=op.f('uq_monsters_key'))
    )


def downgrade() -> None:
    op.drop_table('monsters')
    op.drop_table('personalities')
    op.drop_table('jobs')
    op.drop_table('items')
    op.drop_table('abilities')
