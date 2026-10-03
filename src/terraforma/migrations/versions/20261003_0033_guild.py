"""guild: parties can be open to new members, and teams ask to join them

Revision ID: 0033
Revises: 0032
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0033'
down_revision: str | None = '0032'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('parties', schema=None) as batch_op:
        batch_op.add_column(sa.Column('open', sa.Boolean(), server_default='0', nullable=False))

    op.create_table('party_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('party_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['party_id'], ['parties.id'], name=op.f('fk_party_requests_party_id_parties')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_party_requests_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_party_requests')),
    sa.UniqueConstraint('party_id', 'team_id', name=op.f('uq_party_requests_party_id'))
    )
    with op.batch_alter_table('party_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_party_requests_party_id'), ['party_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_party_requests_team_id'), ['team_id'], unique=False)


def downgrade() -> None:
    # MySQL won't drop an index a foreign key needs; the table is dropped whole instead.
    op.drop_table('party_requests')
    with op.batch_alter_table('parties', schema=None) as batch_op:
        batch_op.drop_column('open')
