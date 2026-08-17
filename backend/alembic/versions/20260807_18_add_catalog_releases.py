"""add_catalog_releases

Revision ID: 20260807_18
Revises: 20260323_17
Create Date: 2026-08-07 18:10:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_18"
down_revision: Union[str, None] = "20260323_17"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "catalog_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=True),
        sa.Column("release_type", sa.String(length=100), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("region", sa.String(length=32), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "source", "external_id", name="uq_catalog_release_source_external"),
    )
    op.create_index("ix_catalog_releases_game_id", "catalog_releases", ["game_id"], unique=False)
    op.create_index("ix_catalog_releases_source", "catalog_releases", ["source"], unique=False)
    op.create_index("ix_catalog_releases_external_id", "catalog_releases", ["external_id"], unique=False)
    op.create_index("ix_catalog_releases_name", "catalog_releases", ["name"], unique=False)
    op.create_index("ix_catalog_releases_code", "catalog_releases", ["code"], unique=False)
    op.create_index("ix_catalog_releases_release_type", "catalog_releases", ["release_type"], unique=False)
    op.create_index("ix_catalog_releases_release_date", "catalog_releases", ["release_date"], unique=False)
    op.create_index("ix_catalog_releases_language", "catalog_releases", ["language"], unique=False)
    op.create_index("ix_catalog_releases_region", "catalog_releases", ["region"], unique=False)

    op.create_table(
        "print_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("print_id", sa.Integer(), nullable=False),
        sa.Column("release_id", sa.Integer(), nullable=False),
        sa.Column("source_print_id", sa.String(length=255), nullable=True),
        sa.Column("appearance_type", sa.String(length=100), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["release_id"], ["catalog_releases.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("print_id", "release_id", name="uq_print_release_identity"),
    )
    op.create_index("ix_print_releases_print_id", "print_releases", ["print_id"], unique=False)
    op.create_index("ix_print_releases_release_id", "print_releases", ["release_id"], unique=False)
    op.create_index("ix_print_releases_source_print_id", "print_releases", ["source_print_id"], unique=False)
    op.create_index("ix_print_releases_appearance_type", "print_releases", ["appearance_type"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_print_releases_appearance_type", table_name="print_releases")
    op.drop_index("ix_print_releases_source_print_id", table_name="print_releases")
    op.drop_index("ix_print_releases_release_id", table_name="print_releases")
    op.drop_index("ix_print_releases_print_id", table_name="print_releases")
    op.drop_table("print_releases")

    op.drop_index("ix_catalog_releases_region", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_language", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_release_date", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_release_type", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_code", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_name", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_external_id", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_source", table_name="catalog_releases")
    op.drop_index("ix_catalog_releases_game_id", table_name="catalog_releases")
    op.drop_table("catalog_releases")
