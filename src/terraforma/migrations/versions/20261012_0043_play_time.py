"""play time: the seconds each account has played, and when each round of a fight was played

Revision ID: 0043
Revises: 0042
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('play_times',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('seconds', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('counted_to', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('last_input_at', sa.BigInteger(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_play_times_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', name=op.f('pk_play_times'))
    )
    with op.batch_alter_table('fight_actions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('played_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('fight_actions', schema=None) as batch_op:
        batch_op.drop_column('played_at')
    op.drop_table('play_times')
