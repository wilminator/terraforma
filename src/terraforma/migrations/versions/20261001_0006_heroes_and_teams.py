"""heroes and teams

Revision ID: 0006
Revises: 0005
Created: 2026-10-01
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0006'
down_revision: str | None = '0005'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('heroes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=24), nullable=False),
    sa.Column('name_key', sa.String(length=24), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=False),
    sa.Column('level', sa.Integer(), nullable=False),
    sa.Column('xp', sa.BigInteger(), nullable=False),
    sa.Column('stats', sa.JSON(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_heroes_account_id_accounts')),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], name=op.f('fk_heroes_job_id_jobs')),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_heroes_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_heroes')),
    sa.UniqueConstraint('account_id', 'name_key', name=op.f('uq_heroes_account_id'))
    )
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_heroes_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_heroes_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_heroes_map_id'), ['map_id'], unique=False)

    op.create_table('teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=24), nullable=False),
    sa.Column('name_key', sa.String(length=24), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_teams_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teams')),
    sa.UniqueConstraint('account_id', 'name_key', name=op.f('uq_teams_account_id'))
    )
    with op.batch_alter_table('teams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_teams_account_id'), ['account_id'], unique=False)

    op.create_table('team_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('slot', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_team_members_hero_id_heroes')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_members_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_members')),
    sa.UniqueConstraint('hero_id', name=op.f('uq_team_members_hero_id')),
    sa.UniqueConstraint('team_id', 'slot', name=op.f('uq_team_members_team_id'))
    )
    with op.batch_alter_table('team_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_team_members_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    op.drop_table('team_members')
    op.drop_table('teams')
    op.drop_table('heroes')
