"""storage: the items a team or an account keeps beside its heroes' packs

Revision ID: 0050
Revises: 0049
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('storage_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('owner_kind', sa.String(length=16), nullable=False),
    sa.Column('owner_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_storage_items_item_id_items')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_storage_items')),
    sa.UniqueConstraint('owner_kind', 'owner_id', 'position', name=op.f('uq_storage_items_owner_kind'))
    )


def downgrade() -> None:
    op.drop_table('storage_items')
