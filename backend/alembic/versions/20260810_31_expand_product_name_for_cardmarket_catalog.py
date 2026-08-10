"""expand canonical product names for Cardmarket catalog

Revision ID: 20260810_31
Revises: 20260810_30
Create Date: 2026-08-10 14:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260810_31"
down_revision: Union[str, None] = "20260810_30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "products",
        "name",
        existing_type=sa.String(length=255),
        type_=sa.String(length=500),
        existing_nullable=False,
    )


def downgrade() -> None:
    # A downgrade after importing long Cardmarket product names would be lossy.
    # Refuse implicitly at the database layer if values exceed 255 characters.
    op.alter_column(
        "products",
        "name",
        existing_type=sa.String(length=500),
        type_=sa.String(length=255),
        existing_nullable=False,
    )
