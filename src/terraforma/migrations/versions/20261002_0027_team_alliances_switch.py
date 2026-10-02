"""a switch for naming a team's alliances on its public page

Revision ID: 0027
Revises: 0026
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0027'
down_revision: str | None = '0026'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column('player_profiles', sa.Column('team_alliances', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('player_profiles', 'team_alliances')
