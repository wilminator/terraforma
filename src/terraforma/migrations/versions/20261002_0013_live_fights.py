"""live fights: the round timer, finished fights and the commands waiting for a round

Revision ID: 0013
Revises: 0012
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0013'
down_revision: str | None = '0012'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.add_column(sa.Column('round_deadline', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('finished', sa.Boolean(), server_default=sa.false(), nullable=False))
        batch_op.create_index(batch_op.f('ix_fights_round_deadline'), ['round_deadline'], unique=False)

    op.create_table('fight_commands',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('round_number', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('party', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('group_index', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('character', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('command', sa.Integer(), nullable=False),
    sa.Column('using_index', sa.Integer(), nullable=False),
    sa.Column('target', sa.JSON(), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_fight_commands_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', 'round_number', 'party', 'group_index', 'character', name=op.f('pk_fight_commands'))
    )


def downgrade() -> None:
    op.drop_table('fight_commands')
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_fights_round_deadline'))
        batch_op.drop_column('finished')
        batch_op.drop_column('round_deadline')
