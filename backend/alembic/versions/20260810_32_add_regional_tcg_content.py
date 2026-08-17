"""add persistent regional tcg content

Revision ID: 20260810_32
Revises: 20260810_31
Create Date: 2026-08-10 15:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260810_32"
down_revision: Union[str, None] = "20260810_31"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "regional_tcg_content",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id", ondelete="CASCADE"), nullable=False),
        sa.Column("region", sa.String(length=16), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=100), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("item_url", sa.Text(), nullable=False),
        sa.Column("title", sa.String(length=1000), nullable=False),
        sa.Column("published_date", sa.Date(), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_key", "region", "item_url", name="uq_regional_tcg_content_identity"),
    )
    op.create_index(
        "ix_regional_tcg_content_game_region_date",
        "regional_tcg_content",
        ["game_id", "region", "published_date"],
    )
    op.create_index(
        "ix_regional_tcg_content_kind_release",
        "regional_tcg_content",
        ["kind", "release_date"],
    )
    op.create_index(
        "ix_regional_tcg_content_source_last_seen",
        "regional_tcg_content",
        ["source_key", "last_seen_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_regional_tcg_content_source_last_seen", table_name="regional_tcg_content")
    op.drop_index("ix_regional_tcg_content_kind_release", table_name="regional_tcg_content")
    op.drop_index("ix_regional_tcg_content_game_region_date", table_name="regional_tcg_content")
    op.drop_table("regional_tcg_content")
