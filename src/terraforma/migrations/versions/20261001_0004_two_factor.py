"""two-factor login

Revision ID: 0004
Revises: 0003
Created: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('totp_secret', sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column('totp_enabled_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('totp_last_step', sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column('recovery_codes', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('twofa_change_nonce', sa.String(length=43), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('twofa_change_nonce')
        batch_op.drop_column('recovery_codes')
        batch_op.drop_column('totp_last_step')
        batch_op.drop_column('totp_enabled_at')
        batch_op.drop_column('totp_secret')
