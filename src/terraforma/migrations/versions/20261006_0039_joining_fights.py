"""joining a running fight: the parties waiting to act, and the hub's offers

Revision ID: 0039
Revises: 0038
Created: 2026-10-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from terraforma.db.dialect import ExactJSON

revision: str = '0039'
down_revision: str | None = '0038'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('fight_joins',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('party', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('event', ExactJSON(), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_fight_joins_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', 'party', name=op.f('pk_fight_joins'))
    )
    op.create_table('join_offers',
    sa.Column('party_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_join_offers_fight_id_fights')),
    sa.PrimaryKeyConstraint('party_id', name=op.f('pk_join_offers'))
    )
    with op.batch_alter_table('join_offers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_join_offers_fight_id'), ['fight_id'], unique=False)


def downgrade() -> None:
    op.drop_table('join_offers')
    op.drop_table('fight_joins')
