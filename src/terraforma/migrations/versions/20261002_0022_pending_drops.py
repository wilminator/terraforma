"""pending drops: drops held for need/want rolls or a hand-out

Revision ID: 0022
Revises: 0021
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('pending_drops',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=False),
    sa.Column('number', sa.Integer(), nullable=False),
    sa.Column('party', sa.Integer(), nullable=False),
    sa.Column('item_key', sa.String(length=64), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('winner_id', sa.Integer(), nullable=True),
    sa.Column('lost', sa.BigInteger(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_pending_drops_fight_id_fights')),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_pending_drops_map_id_maps')),
    sa.ForeignKeyConstraint(['winner_id'], ['heroes.id'], name=op.f('fk_pending_drops_winner_id_heroes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pending_drops')),
    sa.UniqueConstraint('fight_id', 'number', name=op.f('uq_pending_drops_fight_id'))
    )
    with op.batch_alter_table('pending_drops', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_drops_fight_id'), ['fight_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_drops_map_id'), ['map_id'], unique=False)

    op.create_table('pending_drop_choices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('pending_id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('choice', sa.String(length=8), nullable=False),
    sa.Column('roll', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_pending_drop_choices_hero_id_heroes')),
    sa.ForeignKeyConstraint(['pending_id'], ['pending_drops.id'], name=op.f('fk_pending_drop_choices_pending_id_pending_drops')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pending_drop_choices')),
    sa.UniqueConstraint('pending_id', 'hero_id', name=op.f('uq_pending_drop_choices_pending_id'))
    )
    with op.batch_alter_table('pending_drop_choices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_drop_choices_hero_id'), ['hero_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_drop_choices_pending_id'), ['pending_id'], unique=False)


def downgrade() -> None:
    # (no separate index drops: MySQL refuses to drop an index a foreign key needs, and dropping the table takes them)
    op.drop_table('pending_drop_choices')
    op.drop_table('pending_drops')
