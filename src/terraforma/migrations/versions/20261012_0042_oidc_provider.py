"""the sign-in provider: subject ids, one-use codes and access tokens

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
    op.create_table('oidc_codes',
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('redirect_uri', sa.String(length=2048), nullable=False),
    sa.Column('challenge', sa.String(length=43), nullable=False),
    sa.Column('nonce', sa.String(length=256), nullable=True),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('session_version', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.BigInteger(), nullable=False),
    sa.Column('used', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_oidc_codes_account_id_accounts')),
    sa.PrimaryKeyConstraint('code_hash', name=op.f('pk_oidc_codes'))
    )
    with op.batch_alter_table('oidc_codes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_codes_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_codes_expires_at'), ['expires_at'], unique=False)

    op.create_table('oidc_subjects',
    sa.Column('account_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('subject', sa.String(length=43), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_oidc_subjects_account_id_accounts')),
    sa.PrimaryKeyConstraint('account_id', 'client_id', name=op.f('pk_oidc_subjects')),
    sa.UniqueConstraint('subject', name=op.f('uq_oidc_subjects_subject'))
    )
    op.create_table('oidc_tokens',
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('client_id', sa.String(length=64), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('session_version', sa.Integer(), nullable=False),
    sa.Column('code_hash', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_oidc_tokens_account_id_accounts')),
    sa.PrimaryKeyConstraint('token_hash', name=op.f('pk_oidc_tokens'))
    )
    with op.batch_alter_table('oidc_tokens', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_oidc_tokens_account_id'), ['account_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_tokens_code_hash'), ['code_hash'], unique=False)
        batch_op.create_index(batch_op.f('ix_oidc_tokens_expires_at'), ['expires_at'], unique=False)



def downgrade() -> None:
    with op.batch_alter_table('oidc_tokens', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_tokens_expires_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_tokens_code_hash'))
        batch_op.drop_index(batch_op.f('ix_oidc_tokens_account_id'))

    op.drop_table('oidc_tokens')
    op.drop_table('oidc_subjects')
    with op.batch_alter_table('oidc_codes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_oidc_codes_expires_at'))
        batch_op.drop_index(batch_op.f('ix_oidc_codes_account_id'))

    op.drop_table('oidc_codes')
