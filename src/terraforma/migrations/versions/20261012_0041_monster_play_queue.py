"""monster-party play: the volunteer queue, lockouts, seats, and the fight's monster_play flag

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
    op.create_table('monster_volunteers',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('queued_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('checked_in', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_monster_volunteers_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', name=op.f('pk_monster_volunteers'))
    )
    with op.batch_alter_table('monster_volunteers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monster_volunteers_queued_at'), ['queued_at'], unique=False)
    op.create_table('monster_lockouts',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('until', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_monster_lockouts_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', name=op.f('pk_monster_lockouts'))
    )
    op.create_table('monster_seats',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('party', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('seated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_monster_seats_account_id_accounts')),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_monster_seats_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', name=op.f('pk_monster_seats'))
    )
    with op.batch_alter_table('monster_seats', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_monster_seats_account_id'), ['account_id'], unique=False)
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.add_column(sa.Column('monster_play', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.drop_column('monster_play')
    op.drop_table('monster_seats')
    op.drop_table('monster_lockouts')
    op.drop_table('monster_volunteers')
