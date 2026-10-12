"""chat bans: players an admin has barred from speaking

Revision ID: 0042
Revises: 0041
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('chat_bans',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.String(length=280), server_default='', nullable=False),
    sa.Column('by_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_chat_bans_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_bans')),
    sa.UniqueConstraint('account_id', name=op.f('uq_chat_bans_account_id'))
    )


def downgrade() -> None:
    op.drop_table('chat_bans')
