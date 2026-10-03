"""guest pass: a team's standing status can send the team out of its party when it ends

Revision ID: 0035
Revises: 0034
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0035'
down_revision: str | None = '0034'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('standing_statuses', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ends_party', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('standing_statuses', schema=None) as batch_op:
        batch_op.drop_column('ends_party')
