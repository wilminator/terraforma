"""relationships between teams

Revision ID: 0017
Revises: 0016
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0017'
down_revision: str | None = '0016'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('relationships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('subject_kind', sa.String(length=8), nullable=False),
    sa.Column('subject_id', sa.Integer(), nullable=False),
    sa.Column('object_kind', sa.String(length=8), nullable=False),
    sa.Column('object_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('note', sa.String(length=280), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_relationships')),
    sa.UniqueConstraint('subject_kind', 'subject_id', 'object_kind', 'object_id', name=op.f('uq_relationships_subject_kind'))
    )
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_relationships_object_id'), ['object_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_relationships_subject_id'), ['subject_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('relationships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_relationships_subject_id'))
        batch_op.drop_index(batch_op.f('ix_relationships_object_id'))

    op.drop_table('relationships')
