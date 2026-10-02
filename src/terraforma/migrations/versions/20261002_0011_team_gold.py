"""team gold, and a fight's gold paid once

Revision ID: 0011
Revises: 0010
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0011'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gold', sa.BigInteger(), server_default='0', nullable=False))

    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gold_paid', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.drop_column('gold_paid')

    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.drop_column('gold')
