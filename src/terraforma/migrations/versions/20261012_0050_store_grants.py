"""store grants: the store's grant records and the parcels its items wait in

Revision ID: 0050
Revises: 0049
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from terraforma.db.dialect import ExactJSON

revision: str = '0050'
down_revision: str | None = '0049'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('store_grants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('idempotency_key', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('tokens', sa.BigInteger(), server_default='0', nullable=False),
    sa.Column('items', ExactJSON(), nullable=False),
    sa.Column('message', sa.String(length=1000), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_store_grants_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_store_grants')),
    sa.UniqueConstraint('idempotency_key', name=op.f('uq_store_grants_idempotency_key'))
    )
    with op.batch_alter_table('store_grants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_store_grants_account_id'), ['account_id'], unique=False)

    op.create_table('parcels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_parcels_account_id_accounts')),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_parcels_item_id_items')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parcels')),
    sa.UniqueConstraint('account_id', 'item_id', name=op.f('uq_parcels_account_id'))
    )
    with op.batch_alter_table('parcels', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_parcels_account_id'), ['account_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('parcels', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_parcels_account_id'))
    op.drop_table('parcels')
    with op.batch_alter_table('store_grants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_store_grants_account_id'))
    op.drop_table('store_grants')
