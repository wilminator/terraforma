"""guild standing: a team's points and rank with a guild

Revision ID: 0052
Revises: 0051
Created: 2026-10-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0052'
down_revision: str | None = '0051'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('guild_standing',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('guild', sa.String(length=64), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.Column('rank', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_guild_standing_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_guild_standing')),
    sa.UniqueConstraint('team_id', 'guild', name=op.f('uq_guild_standing_team_id'))
    )
    with op.batch_alter_table('guild_standing', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_guild_standing_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    op.drop_table('guild_standing')
