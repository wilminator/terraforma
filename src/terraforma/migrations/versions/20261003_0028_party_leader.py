"""a party's leader: the player who founded it, then who accepts another party into theirs

Revision ID: 0028
Revises: 0027
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0028'
down_revision: str | None = '0027'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('parties', sa.Column('leader_account_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_parties_leader_account_id'), 'parties', ['leader_account_id'], unique=False)
    # Parties that exist already are led by the owner of their first team.
    op.execute(
        "UPDATE parties SET leader_account_id = ("
        "SELECT teams.account_id FROM party_teams JOIN teams ON teams.id = party_teams.team_id "
        "WHERE party_teams.party_id = parties.id AND party_teams.position = 0)"
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_parties_leader_account_id'), table_name='parties')
    op.drop_column('parties', 'leader_account_id')
