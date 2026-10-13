"""public fights: the finished fights a game marked public

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
    op.create_table('public_fights',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_public_fights_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', name=op.f('pk_public_fights'))
    )


def downgrade() -> None:
    op.drop_table('public_fights')
