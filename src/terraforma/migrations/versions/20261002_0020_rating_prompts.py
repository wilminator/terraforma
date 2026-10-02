"""rating prompts: asking a player to rate a team after a fight

Revision ID: 0020
Revises: 0019
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0020'
down_revision: str | None = '0019'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rating_prompts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=False),
    sa.Column('subject_team_id', sa.Integer(), nullable=False),
    sa.Column('object_team_id', sa.Integer(), nullable=False),
    sa.Column('interaction', sa.String(length=8), nullable=False),
    sa.Column('state', sa.String(length=10), nullable=False),
    sa.Column('suggested', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_rating_prompts_fight_id_fights')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rating_prompts')),
    sa.UniqueConstraint('fight_id', 'subject_team_id', 'object_team_id', name=op.f('uq_rating_prompts_fight_id'))
    )
    with op.batch_alter_table('rating_prompts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rating_prompts_fight_id'), ['fight_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_prompts_object_team_id'), ['object_team_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_prompts_subject_team_id'), ['subject_team_id'], unique=False)


def downgrade() -> None:
    op.drop_table('rating_prompts')
