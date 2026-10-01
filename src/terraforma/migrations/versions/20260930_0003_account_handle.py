"""account handle

Revision ID: 0003
Revises: 0002
Created: 2026-09-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('handle', sa.String(length=24), nullable=True))
        batch_op.add_column(sa.Column('handle_key', sa.String(length=24), nullable=True))
        batch_op.create_unique_constraint(batch_op.f('uq_accounts_handle_key'), ['handle_key'])


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('uq_accounts_handle_key'), type_='unique')
        batch_op.drop_column('handle_key')
        batch_op.drop_column('handle')
