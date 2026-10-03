"""Challenge Tokens: the token balances and ledger renamed, with an idempotency key on the ledger

Revision ID: 0029
Revises: 0028
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0029'
down_revision: str | None = '0028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The tables are copied into new ones, not renamed: a renamed table keeps its old constraint names on some databases.
    op.create_table('challenge_balances',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('balance', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_challenge_balances_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', name=op.f('pk_challenge_balances'))
    )
    op.create_table('challenge_ledger',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('amount', sa.BigInteger(), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=True),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_challenge_ledger_account_id_accounts')),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_challenge_ledger_fight_id_fights')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_challenge_ledger')),
    sa.UniqueConstraint('fight_id', 'account_id', name=op.f('uq_challenge_ledger_fight_id')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_challenge_ledger_idempotency_key'))
    )
    with op.batch_alter_table('challenge_ledger', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_challenge_ledger_account_id'), ['account_id'], unique=False)

    op.execute(
        'INSERT INTO challenge_balances (account_id, balance, created_at, updated_at) '
        'SELECT account_id, balance, created_at, updated_at FROM token_balances'
    )
    op.execute(
        'INSERT INTO challenge_ledger (id, account_id, amount, reason, fight_id, created_at, updated_at) '
        'SELECT id, account_id, amount, reason, fight_id, created_at, updated_at FROM token_ledger'
    )
    op.drop_table('token_ledger')
    op.drop_table('token_balances')


def downgrade() -> None:
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

    # Purchases carry no fight and are not earnings; the old tables have no key, so they come back as plain gifts.
    op.execute(
        'INSERT INTO token_balances (account_id, balance, created_at, updated_at) '
        'SELECT account_id, balance, created_at, updated_at FROM challenge_balances'
    )
    op.execute(
        'INSERT INTO token_ledger (id, account_id, amount, reason, fight_id, created_at, updated_at) '
        'SELECT id, account_id, amount, reason, fight_id, created_at, updated_at FROM challenge_ledger'
    )
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    op.drop_table('challenge_ledger')
    op.drop_table('challenge_balances')
