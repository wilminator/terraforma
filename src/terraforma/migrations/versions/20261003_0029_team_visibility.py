"""each team is visible or hidden; the directory opt-in replaces the team-pages and alliances switches

Revision ID: 0029
Revises: 0028
Created: 2026-10-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0029'
down_revision: str | None = '0028'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

teams = sa.table('teams', sa.column('id', sa.Integer), sa.column('account_id', sa.Integer))
players = sa.table('player_profiles', sa.column('account_id', sa.Integer), sa.column('team_pages', sa.Boolean))
rows = sa.table('team_profiles', sa.column('team_id', sa.Integer), sa.column('listed', sa.Boolean), sa.column('visible', sa.Boolean))


def upgrade() -> None:
    with op.batch_alter_table('team_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('visible', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('directory', sa.Boolean(), server_default='0', nullable=False))
    # A team was seen when it was listed and its player had team pages on: that is what visible means now.
    seen = sa.select(teams.c.id).select_from(teams.join(players, players.c.account_id == teams.c.account_id)).where(players.c.team_pages.is_(sa.true()))
    op.execute(rows.update().where(rows.c.listed.is_(sa.true()), rows.c.team_id.in_(seen)).values(visible=sa.true()))
    with op.batch_alter_table('team_profiles', schema=None) as batch_op:
        batch_op.drop_column('listed')
    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.drop_column('team_pages')
        batch_op.drop_column('team_alliances')


def downgrade() -> None:
    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('team_alliances', sa.Boolean(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('team_pages', sa.Boolean(), server_default='0', nullable=False))
    with op.batch_alter_table('team_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('listed', sa.Boolean(), server_default='1', nullable=False))
    op.execute(rows.update().values(listed=rows.c.visible))
    shown = sa.select(teams.c.account_id).select_from(teams.join(rows, rows.c.team_id == teams.c.id)).where(rows.c.visible.is_(sa.true()))
    op.execute(players.update().where(players.c.account_id.in_(shown)).values(team_pages=sa.true()))
    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.drop_column('directory')
    with op.batch_alter_table('team_profiles', schema=None) as batch_op:
        batch_op.drop_column('visible')
