"""weekly quests: quest templates, and the quest each team is offered each week

Revision ID: 0049
Revises: 0048
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0049'
down_revision: str | None = '0048'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('quest_templates',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('min_level', sa.Integer(), nullable=False),
    sa.Column('max_level', sa.Integer(), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_quest_templates')),
    sa.UniqueConstraint('key', name=op.f('uq_quest_templates_key'))
    )
    op.create_table('weekly_quests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('week', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('template_id', sa.Integer(), nullable=True),
    sa.Column('promoted_template_id', sa.Integer(), nullable=True),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('spec', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['promoted_template_id'], ['quest_templates.id'], name=op.f('fk_weekly_quests_promoted_template_id_quest_templates')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_weekly_quests_team_id_teams')),
    sa.ForeignKeyConstraint(['template_id'], ['quest_templates.id'], name=op.f('fk_weekly_quests_template_id_quest_templates')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_weekly_quests')),
    sa.UniqueConstraint('team_id', 'week', name=op.f('uq_weekly_quests_team_id'))
    )
    with op.batch_alter_table('weekly_quests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_weekly_quests_source'), ['source'], unique=False)
        batch_op.create_index(batch_op.f('ix_weekly_quests_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the tables are dropped whole instead.
    op.drop_table('weekly_quests')
    op.drop_table('quest_templates')
