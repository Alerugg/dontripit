"""add One Piece DON market source registry

Revision ID: 20260823_41
Revises: 20260823_40
Create Date: 2026-08-23

Cardmarket DON pages are source-owned commercial identities. They are useful for
names, subject discovery and pricing coverage, but they are not canonical
Don’tRipIt Prints until an independent deterministic crosswalk exists.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260823_41"
down_revision: Union[str, None] = "20260823_40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onepiece_don_market_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("metacard_external_id", sa.String(length=255), nullable=False),
        sa.Column("representative_external_product_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("subject_normalized", sa.Text(), nullable=True),
        sa.Column("product_ids_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("product_count", sa.Integer(), nullable=False),
        sa.Column("source_as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("official_item_id", sa.Integer(), nullable=True),
        sa.Column("mapping_source", sa.String(length=100), nullable=True),
        sa.Column("mapping_confidence", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["official_item_id"], ["onepiece_don_official_items.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("source", "metacard_external_id", name="uq_onepiece_don_market_source_meta"),
    )
    op.create_index(
        "ix_onepiece_don_market_source_subject",
        "onepiece_don_market_items",
        ["source", "subject_normalized"],
    )
    op.create_index(
        "ix_onepiece_don_market_official_item",
        "onepiece_don_market_items",
        ["official_item_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_onepiece_don_market_official_item", table_name="onepiece_don_market_items")
    op.drop_index("ix_onepiece_don_market_source_subject", table_name="onepiece_don_market_items")
    op.drop_table("onepiece_don_market_items")
