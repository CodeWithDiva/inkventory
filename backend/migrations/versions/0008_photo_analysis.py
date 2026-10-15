"""photo analysis: what a vision model saw in each product's photos

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-15 10:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('products') as batch:
        batch.add_column(sa.Column('image_analysis', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('products') as batch:
        batch.drop_column('image_analysis')
