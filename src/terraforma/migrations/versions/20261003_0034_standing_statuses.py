"""standing statuses: status tokens that last between fights, on a hero, a team or a party

Revision ID: 0034
Revises: 0033
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0034'
down_revision: str | None = '0033'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('standing_statuses',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('target_kind', sa.String(length=8), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=64), nullable=False),
    sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('unremovable', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_standing_statuses')),
    sa.UniqueConstraint('target_kind', 'target_id', 'status', name=op.f('uq_standing_statuses_target_kind'))
    )
    with op.batch_alter_table('standing_statuses', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_standing_statuses_ends_at'), ['ends_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_standing_statuses_target_id'), ['target_id'], unique=False)


def downgrade() -> None:
    op.drop_table('standing_statuses')
