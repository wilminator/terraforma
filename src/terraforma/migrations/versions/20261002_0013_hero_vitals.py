"""hero vitals: what a hero's resources stand at between fights

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
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vitals', sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.drop_column('vitals')
