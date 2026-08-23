"""add One Piece DON structured classification

Revision ID: 20260823_40
Revises: 20260823_39
Create Date: 2026-08-23

DON!! cards are an explicit physical-card family. They are never inferred from
free text and their marketplace/source identifiers are not collector numbers.
This migration adds source-backed classification plus a complete official Bandai
PDF inventory surface, and exposes explicit DON flags in Search V2 projections.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260823_40"
down_revision: Union[str, None] = "20260823_39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CARD_INDEX = "ix_card_search_profiles_game_don_subject_pattern"
PRINT_INDEX = "ix_print_search_profiles_game_don_subject_pattern"


def upgrade() -> None:
    op.create_table(
        "onepiece_don_official_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pdf_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("slot_number", sa.Integer(), nullable=False),
        sa.Column("image_object", sa.String(length=32), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("image_phash", sa.String(length=64), nullable=True),
        sa.Column("distribution_label", sa.Text(), nullable=True),
        sa.Column("print_id", sa.Integer(), nullable=True),
        sa.Column("mapping_source", sa.String(length=100), nullable=True),
        sa.Column("mapping_confidence", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("pdf_sha256", "image_object", name="uq_onepiece_don_official_items_image"),
        sa.UniqueConstraint(
            "pdf_sha256", "page_number", "slot_number", name="uq_onepiece_don_official_items_slot"
        ),
    )
    op.create_index("ix_onepiece_don_official_items_pdf_sha256", "onepiece_don_official_items", ["pdf_sha256"])
    op.create_index("ix_onepiece_don_official_items_image_sha256", "onepiece_don_official_items", ["image_sha256"])
    op.create_index("ix_onepiece_don_official_items_image_phash", "onepiece_don_official_items", ["image_phash"])
    op.create_index("ix_onepiece_don_official_items_print_id", "onepiece_don_official_items", ["print_id"])

    op.create_table(
        "onepiece_don_prints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("print_id", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=True),
        sa.Column("subject_normalized", sa.Text(), nullable=True),
        sa.Column("distribution_label", sa.Text(), nullable=True),
        sa.Column("finish", sa.String(length=100), nullable=True),
        sa.Column("official_listed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("official_pdf_sha256", sa.String(length=64), nullable=True),
        sa.Column("official_pdf_page", sa.Integer(), nullable=True),
        sa.Column("official_pdf_slot", sa.Integer(), nullable=True),
        sa.Column("official_image_object", sa.String(length=32), nullable=True),
        sa.Column("subject_source", sa.String(length=100), nullable=True),
        sa.Column("distribution_source", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("print_id", name="uq_onepiece_don_prints_print"),
        sa.UniqueConstraint(
            "official_pdf_sha256",
            "official_image_object",
            name="uq_onepiece_don_prints_official_image",
        ),
    )
    op.create_index("ix_onepiece_don_prints_print_id", "onepiece_don_prints", ["print_id"], unique=True)
    op.create_index("ix_onepiece_don_prints_subject_normalized", "onepiece_don_prints", ["subject_normalized"])
    op.create_index("ix_onepiece_don_prints_official_pdf_sha256", "onepiece_don_prints", ["official_pdf_sha256"])

    op.add_column(
        "card_search_profiles",
        sa.Column("is_don", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "card_search_profiles",
        sa.Column("don_subject_normalized", sa.Text(), nullable=True),
    )
    op.add_column(
        "print_search_profiles",
        sa.Column("is_don", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "print_search_profiles",
        sa.Column("don_subject_normalized", sa.Text(), nullable=True),
    )

    # Prefix searches are the latency-critical path. pattern_ops keeps both the
    # normal and DON-only selected-game flows indexable under non-C collations.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {CARD_INDEX}
            ON card_search_profiles (game_id, is_don, don_subject_normalized text_pattern_ops)
            """
        )
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {PRINT_INDEX}
            ON print_search_profiles (game_id, is_don, don_subject_normalized text_pattern_ops)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {PRINT_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {CARD_INDEX}")

    op.drop_column("print_search_profiles", "don_subject_normalized")
    op.drop_column("print_search_profiles", "is_don")
    op.drop_column("card_search_profiles", "don_subject_normalized")
    op.drop_column("card_search_profiles", "is_don")

    op.drop_index("ix_onepiece_don_prints_official_pdf_sha256", table_name="onepiece_don_prints")
    op.drop_index("ix_onepiece_don_prints_subject_normalized", table_name="onepiece_don_prints")
    op.drop_index("ix_onepiece_don_prints_print_id", table_name="onepiece_don_prints")
    op.drop_table("onepiece_don_prints")

    op.drop_index("ix_onepiece_don_official_items_print_id", table_name="onepiece_don_official_items")
    op.drop_index("ix_onepiece_don_official_items_image_phash", table_name="onepiece_don_official_items")
    op.drop_index("ix_onepiece_don_official_items_image_sha256", table_name="onepiece_don_official_items")
    op.drop_index("ix_onepiece_don_official_items_pdf_sha256", table_name="onepiece_don_official_items")
    op.drop_table("onepiece_don_official_items")
