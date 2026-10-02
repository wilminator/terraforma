"""fights, their participants and the log of their rounds

Revision ID: 0008
Revises: 0007
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('fights',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('guid', sa.String(length=32), nullable=False),
    sa.Column('initial_state', sa.JSON(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_fights_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fights')),
    sa.UniqueConstraint('guid', name=op.f('uq_fights_guid'))
    )
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fights_map_id'), ['map_id'], unique=False)

    op.create_table('fight_participants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=False),
    sa.Column('party', sa.Integer(), nullable=False),
    sa.Column('group_index', sa.Integer(), nullable=False),
    sa.Column('character', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=True),
    sa.Column('monster_key', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_fight_participants_fight_id_fights')),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_fight_participants_hero_id_heroes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fight_participants')),
    sa.UniqueConstraint('fight_id', 'party', 'group_index', 'character', name=op.f('uq_fight_participants_fight_id'))
    )
    with op.batch_alter_table('fight_participants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fight_participants_fight_id'), ['fight_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fight_participants_hero_id'), ['hero_id'], unique=False)

    op.create_table('fight_actions',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('sequence', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('commands', sa.JSON(), nullable=False),
    sa.Column('events', sa.JSON(), nullable=False),
    sa.Column('previous_hash', sa.String(length=64), nullable=False),
    sa.Column('hash', sa.String(length=64), nullable=False),
    sa.Column('final_state', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_fight_actions_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', 'sequence', name=op.f('pk_fight_actions'))
    )


def downgrade() -> None:
    op.drop_table('fight_actions')
    op.drop_table('fight_participants')
    op.drop_table('fights')
