"""ballots in alliances

Revision ID: 0020
Revises: 0018
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0020'
down_revision: str | None = '0018'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('alliance_ballots',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('alliance_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=True),
    sa.Column('secret', sa.Boolean(), nullable=False),
    sa.Column('opened_by_team_id', sa.Integer(), nullable=False),
    sa.Column('opened_at', sa.BigInteger(), nullable=False),
    sa.Column('closes_at', sa.BigInteger(), nullable=True),
    sa.Column('closed_at', sa.BigInteger(), nullable=True),
    sa.Column('result', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['alliance_id'], ['alliances.id'], name=op.f('fk_alliance_ballots_alliance_id_alliances')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_ballots'))
    )
    with op.batch_alter_table('alliance_ballots', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_ballots_alliance_id'), ['alliance_id'], unique=False)

    op.create_table('alliance_ballot_voters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ballot_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ballot_id'], ['alliance_ballots.id'], name=op.f('fk_alliance_ballot_voters_ballot_id_alliance_ballots')),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], name=op.f('fk_alliance_ballot_voters_team_id_teams')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_ballot_voters')),
    sa.UniqueConstraint('ballot_id', 'team_id', name=op.f('uq_alliance_ballot_voters_ballot_id'))
    )
    with op.batch_alter_table('alliance_ballot_voters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_ballot_voters_ballot_id'), ['ballot_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_alliance_ballot_voters_team_id'), ['team_id'], unique=False)

    op.create_table('alliance_ballot_votes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ballot_id', sa.Integer(), nullable=False),
    sa.Column('team_id', sa.Integer(), nullable=True),
    sa.Column('option', sa.Integer(), nullable=False),
    sa.Column('weight', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ballot_id'], ['alliance_ballots.id'], name=op.f('fk_alliance_ballot_votes_ballot_id_alliance_ballots')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_alliance_ballot_votes'))
    )
    with op.batch_alter_table('alliance_ballot_votes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_alliance_ballot_votes_ballot_id'), ['ballot_id'], unique=False)


def downgrade() -> None:
    # (Dropping a table drops its indexes with it: MySQL will not drop an index a foreign key needs.)
    op.drop_table('alliance_ballot_votes')
    op.drop_table('alliance_ballot_voters')
    op.drop_table('alliance_ballots')
