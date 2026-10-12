"""fight tags: each team's result in a finished fight, the tags and lists that hold fights, and the fights kept by hand

Revision ID: 0043
Revises: 0042
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('fight_teams',
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('team_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('party', sa.Integer(), nullable=False),
    sa.Column('pvp', sa.Boolean(), nullable=False),
    sa.Column('won', sa.Boolean(), nullable=False),
    sa.Column('overkill', sa.Double(), nullable=False),
    sa.Column('underdog', sa.Double(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_fight_teams_fight_id_fights')),
    sa.PrimaryKeyConstraint('fight_id', 'team_id', name=op.f('pk_fight_teams'))
    )
    with op.batch_alter_table('fight_teams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_fight_teams_team_id'), ['team_id'], unique=False)

    op.create_table('team_tags',
    sa.Column('team_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('fight_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_team_tags_fight_id_fights')),
    sa.PrimaryKeyConstraint('team_id', 'kind', name=op.f('pk_team_tags'))
    )
    with op.batch_alter_table('team_tags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_team_tags_fight_id'), ['fight_id'], unique=False)

    op.create_table('team_kept',
    sa.Column('team_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_team_kept_fight_id_fights')),
    sa.PrimaryKeyConstraint('team_id', 'fight_id', name=op.f('pk_team_kept'))
    )
    with op.batch_alter_table('team_kept', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_team_kept_fight_id'), ['fight_id'], unique=False)

    op.create_table('alliance_tops',
    sa.Column('alliance_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Double(), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_alliance_tops_fight_id_fights')),
    sa.PrimaryKeyConstraint('alliance_id', 'kind', 'fight_id', name=op.f('pk_alliance_tops'))
    )
    with op.batch_alter_table('alliance_tops', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_tops_fight_id'), ['fight_id'], unique=False)

    op.create_table('alliance_kept',
    sa.Column('alliance_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('fight_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['fight_id'], ['fights.id'], name=op.f('fk_alliance_kept_fight_id_fights')),
    sa.PrimaryKeyConstraint('alliance_id', 'fight_id', name=op.f('pk_alliance_kept'))
    )
    with op.batch_alter_table('alliance_kept', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_kept_fight_id'), ['fight_id'], unique=False)


def downgrade() -> None:
    op.drop_table('alliance_kept')
    op.drop_table('alliance_tops')
    op.drop_table('team_kept')
    op.drop_table('team_tags')
    op.drop_table('fight_teams')
