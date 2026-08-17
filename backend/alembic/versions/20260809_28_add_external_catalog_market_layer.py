"""add external catalog market layer

Revision ID: 20260809_28
Revises: 20260809_27
Create Date: 2026-08-09 22:18:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_28"
down_revision: Union[str, None] = "20260809_27"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "external_catalog_products",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("product_group", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=500), nullable=False),
        sa.Column("category_id", sa.String(length=64), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("expansion_external_id", sa.String(length=255), nullable=True),
        sa.Column("date_added", sa.Date(), nullable=True),
        sa.Column("metacard_external_id", sa.String(length=255), nullable=True),
        sa.Column("website_path", sa.Text(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_external_catalog_source_id"),
    )
    op.create_index("ix_external_catalog_products_source", "external_catalog_products", ["source"])
    op.create_index("ix_external_catalog_products_external_id", "external_catalog_products", ["external_id"])
    op.create_index("ix_external_catalog_products_game_id", "external_catalog_products", ["game_id"])
    op.create_index("ix_external_catalog_products_product_group", "external_catalog_products", ["product_group"])
    op.create_index("ix_external_catalog_products_name", "external_catalog_products", ["name"])
    op.create_index("ix_external_catalog_products_category", "external_catalog_products", ["category"])
    op.create_index("ix_external_catalog_products_expansion_external_id", "external_catalog_products", ["expansion_external_id"])
    op.create_index("ix_external_catalog_products_metacard_external_id", "external_catalog_products", ["metacard_external_id"])
    op.create_index("ix_external_catalog_products_source_updated_at", "external_catalog_products", ["source_updated_at"])
    op.create_index("ix_external_catalog_products_last_seen_at", "external_catalog_products", ["last_seen_at"])
    op.create_index(
        "ix_external_catalog_source_game_group",
        "external_catalog_products",
        ["source", "game_id", "product_group"],
    )
    op.create_index(
        "ix_external_catalog_source_expansion",
        "external_catalog_products",
        ["source", "expansion_external_id"],
    )

    op.create_table(
        "external_catalog_print_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "external_product_id",
            sa.Integer(),
            sa.ForeignKey("external_catalog_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("mapping_method", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("link_status", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("external_product_id", "print_id", name="uq_external_catalog_print_link"),
    )
    op.create_index("ix_external_catalog_print_links_external_product_id", "external_catalog_print_links", ["external_product_id"])
    op.create_index("ix_external_catalog_print_links_print_id", "external_catalog_print_links", ["print_id"])
    op.create_index(
        "ix_external_catalog_print_link_status",
        "external_catalog_print_links",
        ["link_status", "confidence"],
    )

    op.create_table(
        "external_catalog_product_variant_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "external_product_id",
            sa.Integer(),
            sa.ForeignKey("external_catalog_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "product_variant_id",
            sa.Integer(),
            sa.ForeignKey("product_variants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mapping_method", sa.String(length=100), nullable=False),
        sa.Column("confidence", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("link_status", sa.String(length=32), server_default="candidate", nullable=False),
        sa.Column("reviewed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "external_product_id",
            "product_variant_id",
            name="uq_external_catalog_variant_link",
        ),
    )
    op.create_index(
        "ix_external_catalog_product_variant_links_external_product_id",
        "external_catalog_product_variant_links",
        ["external_product_id"],
    )
    op.create_index(
        "ix_external_catalog_product_variant_links_product_variant_id",
        "external_catalog_product_variant_links",
        ["product_variant_id"],
    )
    op.create_index(
        "ix_external_catalog_variant_link_status",
        "external_catalog_product_variant_links",
        ["link_status", "confidence"],
    )

    op.create_table(
        "external_market_price_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "external_product_id",
            sa.Integer(),
            sa.ForeignKey("external_catalog_products.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_variant", sa.String(length=32), nullable=False),
        sa.Column("price_low", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_mid", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_market", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_last", sa.Numeric(12, 2), nullable=True),
        sa.Column("avg1", sa.Numeric(12, 2), nullable=True),
        sa.Column("avg7", sa.Numeric(12, 2), nullable=True),
        sa.Column("avg30", sa.Numeric(12, 2), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.UniqueConstraint(
            "external_product_id",
            "currency",
            "price_variant",
            "as_of",
            name="uq_external_market_price_identity",
        ),
    )
    op.create_index(
        "ix_external_market_price_snapshots_external_product_id",
        "external_market_price_snapshots",
        ["external_product_id"],
    )
    op.create_index("ix_external_market_price_snapshots_currency", "external_market_price_snapshots", ["currency"])
    op.create_index(
        "ix_external_market_price_snapshots_price_variant",
        "external_market_price_snapshots",
        ["price_variant"],
    )
    op.create_index("ix_external_market_price_snapshots_as_of", "external_market_price_snapshots", ["as_of"])
    op.create_index(
        "ix_external_market_price_product_variant_asof",
        "external_market_price_snapshots",
        ["external_product_id", "price_variant", "as_of"],
    )


def downgrade() -> None:
    op.drop_index("ix_external_market_price_product_variant_asof", table_name="external_market_price_snapshots")
    op.drop_index("ix_external_market_price_snapshots_as_of", table_name="external_market_price_snapshots")
    op.drop_index("ix_external_market_price_snapshots_price_variant", table_name="external_market_price_snapshots")
    op.drop_index("ix_external_market_price_snapshots_currency", table_name="external_market_price_snapshots")
    op.drop_index("ix_external_market_price_snapshots_external_product_id", table_name="external_market_price_snapshots")
    op.drop_table("external_market_price_snapshots")

    op.drop_index("ix_external_catalog_variant_link_status", table_name="external_catalog_product_variant_links")
    op.drop_index(
        "ix_external_catalog_product_variant_links_product_variant_id",
        table_name="external_catalog_product_variant_links",
    )
    op.drop_index(
        "ix_external_catalog_product_variant_links_external_product_id",
        table_name="external_catalog_product_variant_links",
    )
    op.drop_table("external_catalog_product_variant_links")

    op.drop_index("ix_external_catalog_print_link_status", table_name="external_catalog_print_links")
    op.drop_index("ix_external_catalog_print_links_print_id", table_name="external_catalog_print_links")
    op.drop_index("ix_external_catalog_print_links_external_product_id", table_name="external_catalog_print_links")
    op.drop_table("external_catalog_print_links")

    op.drop_index("ix_external_catalog_source_expansion", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_source_game_group", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_last_seen_at", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_source_updated_at", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_metacard_external_id", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_expansion_external_id", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_category", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_name", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_product_group", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_game_id", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_external_id", table_name="external_catalog_products")
    op.drop_index("ix_external_catalog_products_source", table_name="external_catalog_products")
    op.drop_table("external_catalog_products")
