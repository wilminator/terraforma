"""drop tables, what a monster drops, and a fight's drops saved once

Revision ID: 0015
Revises: 0014
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0015'
down_revision: str | None = '0014'  # trading (#29) also takes 0015: whichever merges second re-points
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('drop_tables',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('weighted', sa.Boolean(), nullable=False),
    sa.Column('rolls', sa.Integer(), nullable=False),
    sa.Column('entries', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_drop_tables')),
    sa.UniqueConstraint('key', name=op.f('uq_drop_tables_key'))
    )
    with op.batch_alter_table('monsters', schema=None) as batch_op:
        batch_op.add_column(sa.Column('drops', sa.JSON(), nullable=True))

    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.add_column(sa.Column('drops_saved', sa.Boolean(), server_default=sa.false(), nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('fights', schema=None) as batch_op:
        batch_op.drop_column('drops_saved')

    with op.batch_alter_table('monsters', schema=None) as batch_op:
        batch_op.drop_column('drops')

    op.drop_table('drop_tables')
