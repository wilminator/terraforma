"""alliances of teams

Revision ID: 0018
Revises: 0017
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0018'
down_revision: str | None = '0017'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('alliances',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=32), nullable=False),
    sa.Column('name_key', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliances')),
    sa.UniqueConstraint('name_key', name=op.f('uq_alliances_name_key'))
    )
    op.create_table('alliance_invites',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alliance_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('invited_by_team_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['alliance_id'], ['alliances.id'], name=op.f('fk_alliance_invites_alliance_id_alliances')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_alliance_invites_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_invites')),
    sa.UniqueConstraint('alliance_id', 'team_id', name=op.f('uq_alliance_invites_alliance_id'))
    )
    with op.batch_alter_table('alliance_invites', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_invites_alliance_id'), ['alliance_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alliance_invites_team_id'), ['team_id'], unique=False)

    op.create_table('alliance_members',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alliance_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['alliance_id'], ['alliances.id'], name=op.f('fk_alliance_members_alliance_id_alliances')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_alliance_members_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_members')),
    sa.UniqueConstraint('alliance_id', 'team_id', name=op.f('uq_alliance_members_alliance_id'))
    )
    with op.batch_alter_table('alliance_members', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_members_alliance_id'), ['alliance_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alliance_members_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    # (Dropping a table drops its indexes with it: MySQL will not drop an index a foreign key needs.)
    op.drop_table('alliance_members')
    op.drop_table('alliance_invites')
    op.drop_table('alliances')
