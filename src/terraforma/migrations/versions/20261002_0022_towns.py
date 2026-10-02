"""towns: a party suspended into its teams, and who is ready to leave

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
    op.create_table('town_visits',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('party_id', sa.Integer(), nullable=False),
    sa.Column('formation', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['party_id'], ['parties.id'], name=op.f('fk_town_visits_party_id_parties')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_town_visits')),
    sa.UniqueConstraint('party_id', name=op.f('uq_town_visits_party_id'))
    )
    op.create_table('town_teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('visit_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('group', sa.Integer(), nullable=False),
    sa.Column('waiting', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_town_teams_team_id_teams')),
    sa.ForeignKeyConstraint(['visit_id'], ['town_visits.id'], name=op.f('fk_town_teams_visit_id_town_visits')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_town_teams')),
    sa.UniqueConstraint('team_id', name=op.f('uq_town_teams_team_id'))
    )
    with op.batch_alter_table('town_teams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_town_teams_visit_id'), ['visit_id'], unique=False)

    op.create_table('town_notices',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('about_team_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['about_team_id'], ['teams.id'], name=op.f('fk_town_notices_about_team_id_teams')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_town_notices_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_town_notices')),
    sa.UniqueConstraint('team_id', 'about_team_id', name=op.f('uq_town_notices_team_id'))
    )
    with op.batch_alter_table('town_notices', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_town_notices_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    op.drop_table('town_notices')
    op.drop_table('town_teams')
    op.drop_table('town_visits')
