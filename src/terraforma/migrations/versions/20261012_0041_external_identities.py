"""external_identities: accounts at external sign-in providers, and accounts that may have no password

Revision ID: 0041
Revises: 0040
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0041'
down_revision: str | None = '0040'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('external_identities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('subject', sa.String(length=255), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_external_identities_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_external_identities')),
    sa.UniqueConstraint('account_id', 'provider', name=op.f('uq_external_identities_account_id')),
    sa.UniqueConstraint('provider', 'subject', name=op.f('uq_external_identities_provider'))
    )
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=True)


def downgrade() -> None:
    # An account without a password cannot exist in the old shape: it gets one nobody can match (not an Argon2 hash).
    op.execute(sa.text("UPDATE accounts SET password_hash = '!' WHERE password_hash IS NULL"))
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.alter_column('password_hash', existing_type=sa.String(length=255), nullable=False)
    op.drop_table('external_identities')
