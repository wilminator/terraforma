"""voice rooms: the room a chat channel has at the voice provider

Revision ID: 0047
Revises: 0046
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0047'
down_revision: str | None = '0046'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('voice_rooms',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('provider_id', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_voice_rooms')),
    sa.UniqueConstraint('kind', 'key', name=op.f('uq_voice_rooms_kind'))
    )


def downgrade() -> None:
    op.drop_table('voice_rooms')
