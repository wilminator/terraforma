"""webauthn_keys and remembered_browsers: security keys as a second factor, and browsers that may skip it

Revision ID: 0052
Revises: 0051
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0052'
down_revision: str | None = '0051'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('webauthn_keys',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('credential_id', sa.String(length=255), nullable=False),
    sa.Column('public_key', sa.String(length=1024), nullable=False),
    sa.Column('sign_count', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=40), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_webauthn_keys_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_webauthn_keys')),
    sa.UniqueConstraint('credential_id', name=op.f('uq_webauthn_keys_credential_id'))
    )
    with op.batch_alter_table('webauthn_keys', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_webauthn_keys_account_id'), ['account_id'], unique=False)

    op.create_table('remembered_browsers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('secret_hash', sa.String(length=64), nullable=False),
    sa.Column('session_version', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('label', sa.String(length=80), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_remembered_browsers_account_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_remembered_browsers')),
    sa.UniqueConstraint('secret_hash', name=op.f('uq_remembered_browsers_secret_hash'))
    )
    with op.batch_alter_table('remembered_browsers', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_remembered_browsers_account_id'), ['account_id'], unique=False)

    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('webauthn_count', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    with op.batch_alter_table('accounts', schema=None) as batch_op:
        batch_op.drop_column('webauthn_count')

    # Dropping a table takes its indexes with it; MySQL refuses to drop an index a foreign key needs.
    op.drop_table('remembered_browsers')
    op.drop_table('webauthn_keys')
