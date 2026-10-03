"""shops: a place on a map where a hero standing on its tile may buy and sell

Revision ID: 0031
Revises: 0030
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0031'
down_revision: str | None = '0030'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('shops',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_shops_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_shops')),
    sa.UniqueConstraint('key', name=op.f('uq_shops_key'))
    )
    with op.batch_alter_table('shops', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_shops_map_id'), ['map_id'], unique=False)


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the table is dropped whole instead.
    op.drop_table('shops')
