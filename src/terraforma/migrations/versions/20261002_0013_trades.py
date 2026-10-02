"""the trade ledger

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
    op.create_table('trades',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('giver_id', sa.Integer(), nullable=False),
    sa.Column('receiver_id', sa.Integer(), nullable=False),
    sa.Column('giver_name', sa.String(length=24), nullable=False),
    sa.Column('receiver_name', sa.String(length=24), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('item_key', sa.String(length=64), nullable=False),
    sa.Column('qty', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_trades'))
    )
    with op.batch_alter_table('trades', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_trades_giver_id'), ['giver_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_trades_receiver_id'), ['receiver_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('trades', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_trades_receiver_id'))
        batch_op.drop_index(batch_op.f('ix_trades_giver_id'))

    op.drop_table('trades')
