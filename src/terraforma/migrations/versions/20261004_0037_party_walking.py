"""a party counts its steps and keeps the route it is walking

Revision ID: 0037
Revises: 0036
Created: 2026-10-04
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0037'
down_revision: str | None = '0036'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('parties', schema=None) as batch_op:
        batch_op.add_column(sa.Column('steps', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('walked', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('route', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('parties', schema=None) as batch_op:
        batch_op.drop_column('route')
        batch_op.drop_column('walked')
        batch_op.drop_column('steps')
