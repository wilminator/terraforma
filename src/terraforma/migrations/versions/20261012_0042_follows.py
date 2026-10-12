"""follows, profile bans and the directory link

Revision ID: 0042
Revises: 0041
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0042'
down_revision: str | None = '0041'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('follows',
    sa.Column('follower_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('followed_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('share_back', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['followed_id'], ['accounts.id'], name=op.f('fk_follows_followed_id_accounts')),
    sa.ForeignKeyConstraint(['follower_id'], ['accounts.id'], name=op.f('fk_follows_follower_id_accounts')),
    sa.PrimaryKeyConstraint('follower_id', 'followed_id', name=op.f('pk_follows'))
    )
    with op.batch_alter_table('follows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_follows_followed_id'), ['followed_id'], unique=False)

    op.create_table('profile_bans',
    sa.Column('owner_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('banned_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['banned_id'], ['accounts.id'], name=op.f('fk_profile_bans_banned_id_accounts')),
    sa.ForeignKeyConstraint(['owner_id'], ['accounts.id'], name=op.f('fk_profile_bans_owner_id_accounts')),
    sa.PrimaryKeyConstraint('owner_id', 'banned_id', name=op.f('pk_profile_bans'))
    )
    with op.batch_alter_table('profile_bans', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_profile_bans_banned_id'), ['banned_id'], unique=False)

    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.add_column(sa.Column('directory_link', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('player_profiles', schema=None) as batch_op:
        batch_op.drop_column('directory_link')
    op.drop_table('profile_bans')
    op.drop_table('follows')
