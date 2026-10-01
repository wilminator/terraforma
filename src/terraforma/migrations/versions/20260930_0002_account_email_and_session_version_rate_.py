"""account email and session version, rate limits

Revision ID: 0002
Revises: 0001
Created: 2026-09-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rate_limit_hits',
    sa.Column('bucket', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=64), nullable=False),
    sa.Column('window_start', sa.BigInteger(), autoincrement=False, nullable=False),
    sa.Column('hits', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('bucket', 'subject', 'window_start', name=op.f('pk_rate_limit_hits'))
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email', sa.String(length=254), nullable=True))
        batch_op.add_column(sa.Column('email_key', sa.String(length=254), nullable=True))
        batch_op.add_column(sa.Column('email_confirmed_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('session_version', sa.Integer(), server_default='0', nullable=False))
        batch_op.create_unique_constraint(batch_op.f('uq_accounts_email_key'), ['email_key'])



def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_accounts_email_key'), type_='unique')
        batch_op.drop_column('session_version')
        batch_op.drop_column('email_confirmed_at')
        batch_op.drop_column('email_key')
        batch_op.drop_column('email')

    op.drop_table('rate_limit_hits')
