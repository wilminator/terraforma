"""admin flag, token balances and the token ledger

Revision ID: 0026
Revises: 0025
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0026'
down_revision: str | None = '0025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('token_balances',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('balance', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_token_balances_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', name=op.f('pk_token_balances'))
    )
    op.create_table('token_ledger',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_token_ledger_account_id_accounts')),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_token_ledger_fight_id_fights')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_token_ledger')),
    sa.UniqueConstraint('fight_id', 'account_id', name=op.f('uq_token_ledger_fight_id'))
    )
    with op.batch_alter_table('token_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_token_ledger_account_id'), ['account_id'], unique=False)

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_admin', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('is_admin')

    op.drop_table('token_ledger')
    op.drop_table('token_balances')
