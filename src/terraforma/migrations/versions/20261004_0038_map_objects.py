"""map objects and edge events: things on a map that run a script, and conversations with them

Revision ID: 0038
Revises: 0037
Created: 2026-10-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0038'
down_revision: str | None = '0037'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('map_objects',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('kind', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('dialog', sa.Text(), nullable=False),
    sa.Column('edge', sa.String(length=5), nullable=True),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_map_objects_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_map_objects')),
    sa.UniqueConstraint('map_id', 'key', name=op.f('uq_map_objects_map_id'))
    )
    with op.batch_alter_table('map_objects', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_map_objects_map_id'), ['map_id'], unique=False)

    with op.batch_alter_table('npc_talks', schema=None) as batch_op:
        batch_op.add_column(sa.Column('object_id', sa.Integer(), nullable=True))
        batch_op.alter_column('npc_id', existing_type=sa.Integer(), nullable=True)
        batch_op.create_index(batch_op.f('ix_npc_talks_object_id'), ['object_id'], unique=False)
        batch_op.create_foreign_key(batch_op.f('fk_npc_talks_object_id_map_objects'), 'map_objects', ['object_id'], ['id'])


def downgrade() -> None:
    with op.batch_alter_table('npc_talks', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_npc_talks_object_id_map_objects'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_npc_talks_object_id'))
        batch_op.alter_column('npc_id', existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column('object_id')

    with op.batch_alter_table('map_objects', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_map_objects_map_id'))

    op.drop_table('map_objects')
