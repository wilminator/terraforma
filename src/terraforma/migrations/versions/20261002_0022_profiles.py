"""profiles of players, teams and alliances

Revision ID: 0022
Revises: 0021
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0022'
down_revision: str | None = '0021'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('player_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=32), nullable=False),
    sa.Column('bio', sa.String(length=500), server_default='', nullable=False),
    sa.Column('team_pages', sa.Boolean(), server_default='0', nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_player_profiles_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_player_profiles')),
    sa.UniqueConstraint('account_id', name=op.f('uq_player_profiles_account_id')),
    sa.UniqueConstraint('token', name=op.f('uq_player_profiles_token'))
    )
    op.create_table('team_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=32), nullable=False),
    sa.Column('listed', sa.Boolean(), server_default='1', nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_team_profiles_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_team_profiles')),
    sa.UniqueConstraint('team_id', name=op.f('uq_team_profiles_team_id')),
    sa.UniqueConstraint('token', name=op.f('uq_team_profiles_token'))
    )
    op.create_table('alliance_profiles',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alliance_id', sa.Integer(), nullable=False),
    sa.Column('token', sa.String(length=32), nullable=False),
    sa.Column('bio', sa.String(length=500), server_default='', nullable=False),
    sa.ForeignKeyConstraint(['alliance_id'], ['alliances.id'], name=op.f('fk_alliance_profiles_alliance_id_alliances')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_profiles')),
    sa.UniqueConstraint('alliance_id', name=op.f('uq_alliance_profiles_alliance_id')),
    sa.UniqueConstraint('token', name=op.f('uq_alliance_profiles_token'))
    )


def downgrade() -> None:
    op.drop_table('alliance_profiles')
    op.drop_table('team_profiles')
    op.drop_table('player_profiles')
