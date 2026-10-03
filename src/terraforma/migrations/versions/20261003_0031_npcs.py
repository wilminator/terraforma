"""npcs: people who stand on a map, and the conversation a hero is in

Revision ID: 0031
Revises: 0030
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0031'
down_revision: str | None = '0030'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('npcs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('dialog', sa.Text(), nullable=False),
    sa.Column('counter', sa.JSON(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_npcs_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_npcs')),
    sa.UniqueConstraint('key', name=op.f('uq_npcs_key'))
    )
    with op.batch_alter_table('npcs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_npcs_map_id'), ['map_id'], unique=False)

    op.create_table('npc_talks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('npc_id', sa.Integer(), nullable=False),
    sa.Column('pos', sa.Integer(), nullable=False),
    sa.Column('prompt', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_npc_talks_hero_id_heroes')),
    sa.ForeignKeyConstraint(['npc_id'], ['npcs.id'], name=op.f('fk_npc_talks_npc_id_npcs')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_npc_talks')),
    sa.UniqueConstraint('hero_id', name=op.f('uq_npc_talks_hero_id'))
    )
    with op.batch_alter_table('npc_talks', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_npc_talks_npc_id'), ['npc_id'], unique=False)


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    op.drop_table('npc_talks')
    op.drop_table('npcs')
