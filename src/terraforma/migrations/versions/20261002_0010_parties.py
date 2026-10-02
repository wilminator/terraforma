"""parties: collections of whole teams

Revision ID: 0010
Revises: 0009
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('parties',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('x', sa.Integer(), nullable=False),
    sa.Column('y', sa.Integer(), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['map_id'], ['maps.id'], name=op.f('fk_parties_map_id_maps')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_parties'))
    )
    with op.batch_alter_table('parties', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_parties_map_id'), ['map_id'], unique=False)

    op.create_table('party_teams',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('party_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['party_id'], ['parties.id'], name=op.f('fk_party_teams_party_id_parties')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_party_teams_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_party_teams')),
    sa.UniqueConstraint('party_id', 'position', name=op.f('uq_party_teams_party_id')),
    sa.UniqueConstraint('team_id', name=op.f('uq_party_teams_team_id'))
    )
    with op.batch_alter_table('party_teams', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_party_teams_party_id'), ['party_id'], unique=False)


def downgrade() -> None:
    op.drop_table('party_teams')
    op.drop_table('parties')
