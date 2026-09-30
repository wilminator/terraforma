"""worlds, maps, accounts and fighters

Revision ID: 0001
Revises: 
Created: 2026-09-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('accounts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=32), nullable=False),
    sa.Column('username_key', sa.String(length=32), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_accounts')),
    sa.UniqueConstraint('username_key', name=op.f('uq_accounts_username_key'))
    )
    op.create_table('worlds',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('seed', sa.BigInteger(), nullable=False),
    sa.Column('tick', sa.BigInteger(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_worlds')),
    sa.UniqueConstraint('name', name=op.f('uq_worlds_name'))
    )
    op.create_table('maps',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('world_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('width', sa.Integer(), nullable=False),
    sa.Column('height', sa.Integer(), nullable=False),
    sa.Column('wraps', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['world_id'], ['worlds.id'], name=op.f('fk_maps_world_id_worlds')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_maps')),
    sa.UniqueConstraint('world_id', 'name', name=op.f('uq_maps_world_id'))
    )
    with op.batch_alter_table('maps', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_maps_world_id'), ['world_id'], unique=False)

    op.create_table('fighters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_fighters_account_id_accounts')),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_fighters_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fighters'))
    )
    with op.batch_alter_table('fighters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fighters_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_fighters_map_id'), ['map_id'], unique=False)



def downgrade() -> None:
    # Dropping a table drops its indexes; dropping them first fails on
    # MySQL, where the foreign keys still need them.
    op.drop_table('fighters')
    op.drop_table('maps')
    op.drop_table('worlds')
    op.drop_table('accounts')
