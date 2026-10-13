"""quest instances

Revision ID: 0050
Revises: 0049
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('quest_instances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('quest', sa.String(length=64), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('capacity', sa.Integer(), nullable=False),
    sa.Column('joinable_until', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(length=16), server_default='open', nullable=False),
    sa.Column('winner_party_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_quest_instances_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quest_instances'))
    )
    with op.batch_alter_table('quest_instances', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quest_instances_map_id'), ['map_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quest_instances_quest'), ['quest'], unique=False)

    op.create_table('instance_parties',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('instance_id', sa.Integer(), nullable=False),
    sa.Column('party_id', sa.Integer(), nullable=False),
    sa.Column('from_map_id', sa.Integer(), nullable=False),
    sa.Column('from_x', sa.Integer(), nullable=False),
    sa.Column('from_y', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['from_map_id'], ['maps.id'], name=op.f('fk_instance_parties_from_map_id_maps')),
    sa.ForeignKeyConstraint(['instance_id'], ['quest_instances.id'], name=op.f('fk_instance_parties_instance_id_quest_instances')),
    sa.ForeignKeyConstraint(['party_id'], ['parties.id'], name=op.f('fk_instance_parties_party_id_parties')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_instance_parties')),
    sa.UniqueConstraint('party_id', name=op.f('uq_instance_parties_party_id'))
    )
    with op.batch_alter_table('instance_parties', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_instance_parties_instance_id'), ['instance_id'], unique=False)


def downgrade() -> None:
    op.drop_table('instance_parties')
    op.drop_table('quest_instances')
