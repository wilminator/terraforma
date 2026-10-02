"""hero gold, inventory, equipment and abilities

Revision ID: 0007
Revises: 0006
Created: 2026-10-02
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0007'
down_revision: str | None = '0006'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gold', sa.BigInteger(), server_default='0', nullable=False))

    op.create_table('hero_items',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('qty', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_hero_items_hero_id_heroes')),
    sa.ForeignKeyConstraint(['item_id'], ['items.id'], name=op.f('fk_hero_items_item_id_items')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hero_items'))
    )
    with op.batch_alter_table('hero_items', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hero_items_hero_id'), ['hero_id'], unique=False)

    op.create_table('hero_equipment',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('slot', sa.String(length=32), nullable=False),
    sa.Column('hero_item_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_hero_equipment_hero_id_heroes')),
    sa.ForeignKeyConstraint(['hero_item_id'], ['hero_items.id'], name=op.f('fk_hero_equipment_hero_item_id_hero_items')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hero_equipment')),
    sa.UniqueConstraint('hero_id', 'slot', name=op.f('uq_hero_equipment_hero_id'))
    )
    with op.batch_alter_table('hero_equipment', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hero_equipment_hero_id'), ['hero_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_hero_equipment_hero_item_id'), ['hero_item_id'], unique=False)

    op.create_table('hero_abilities',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('hero_id', sa.Integer(), nullable=False),
    sa.Column('ability_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['ability_id'], ['abilities.id'], name=op.f('fk_hero_abilities_ability_id_abilities')),
    sa.ForeignKeyConstraint(['hero_id'], ['heroes.id'], name=op.f('fk_hero_abilities_hero_id_heroes')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_hero_abilities')),
    sa.UniqueConstraint('hero_id', 'ability_id', name=op.f('uq_hero_abilities_hero_id'))
    )
    with op.batch_alter_table('hero_abilities', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_hero_abilities_hero_id'), ['hero_id'], unique=False)


def downgrade() -> None:
    op.drop_table('hero_abilities')
    op.drop_table('hero_equipment')
    op.drop_table('hero_items')
    with op.batch_alter_table('heroes', schema=None) as batch_op:
        batch_op.drop_column('gold')
