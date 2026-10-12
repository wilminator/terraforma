"""chat: messages, mutes and flags

Revision ID: 0043
Revises: 0042
Created: 2026-10-12
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0043'
down_revision: str | None = '0042'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('chat_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('key', sa.String(length=40), nullable=False),
    sa.Column('sender_id', sa.Integer(), nullable=False),
    sa.Column('body', sa.String(length=500), nullable=False),
    sa.Column('map_id', sa.Integer(), nullable=True),
    sa.Column('x', sa.Integer(), nullable=True),
    sa.Column('y', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['sender_id'], ['accounts.id'], name=op.f('fk_chat_messages_sender_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_messages'))
    )
    with op.batch_alter_table('chat_messages', schema=None) as batch_op:
        batch_op.create_index('ix_chat_messages_channel', ['kind', 'key', 'id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_messages_sender_id'), ['sender_id'], unique=False)

    op.create_table('chat_mutes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('account_id', sa.Integer(), nullable=False),
    sa.Column('muted_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['account_id'], ['accounts.id'], name=op.f('fk_chat_mutes_account_id_accounts')),
    sa.ForeignKeyConstraint(['muted_id'], ['accounts.id'], name=op.f('fk_chat_mutes_muted_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_mutes')),
    sa.UniqueConstraint('account_id', 'muted_id', name=op.f('uq_chat_mutes_account_id'))
    )
    with op.batch_alter_table('chat_mutes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_mutes_account_id'), ['account_id'], unique=False)

    op.create_table('chat_flags',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('message_id', sa.Integer(), nullable=False),
    sa.Column('reporter_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=16), nullable=False),
    sa.Column('note', sa.String(length=280), server_default='', nullable=False),
    sa.Column('state', sa.String(length=10), server_default='open', nullable=False),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['chat_messages.id'], name=op.f('fk_chat_flags_message_id_chat_messages')),
    sa.ForeignKeyConstraint(['reporter_id'], ['accounts.id'], name=op.f('fk_chat_flags_reporter_id_accounts')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_chat_flags')),
    sa.UniqueConstraint('message_id', 'reporter_id', name=op.f('uq_chat_flags_message_id'))
    )
    with op.batch_alter_table('chat_flags', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_chat_flags_message_id'), ['message_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_chat_flags_state'), ['state'], unique=False)


def downgrade() -> None:
    op.drop_table('chat_flags')
    op.drop_table('chat_mutes')
    op.drop_table('chat_messages')
