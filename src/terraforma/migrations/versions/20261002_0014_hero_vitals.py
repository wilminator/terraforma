"""hero vitals: what a hero's resources stand at between fights

Revision ID: 0014
Revises: 0013
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0014'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('vitals', sa.JSON(none_as_null=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.drop_column('vitals')
