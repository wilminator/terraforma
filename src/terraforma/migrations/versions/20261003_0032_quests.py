"""quests: a team's permanent record of completed quests, and its markers for quests in progress

Revision ID: 0032
Revises: 0031
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0032'
down_revision: str | None = '0031'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('quest_records',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('category', sa.String(length=64), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_quest_records_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quest_records')),
    sa.UniqueConstraint('team_id', 'category', 'level', name=op.f('uq_quest_records_team_id'))
    )
    with op.batch_alter_table('quest_records', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quest_records_team_id'), ['team_id'], unique=False)

    op.create_table('quest_markers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('quest', sa.String(length=64), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_quest_markers_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quest_markers')),
    sa.UniqueConstraint('team_id', 'quest', name=op.f('uq_quest_markers_team_id'))
    )
    with op.batch_alter_table('quest_markers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quest_markers_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    op.drop_table('quest_markers')
    op.drop_table('quest_records')
