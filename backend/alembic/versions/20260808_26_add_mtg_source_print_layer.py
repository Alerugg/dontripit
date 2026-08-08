"""add minimal MTG SourcePrint identity and snapshot provenance

Revision ID: 20260808_26
Revises: 20260808_25
Create Date: 2026-08-08 19:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260808_26"
down_revision: Union[str, None] = "20260808_25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "mtg_source_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=True),
        sa.Column("counts_json", jsonb, nullable=True),
        sa.Column("metadata_json", jsonb, nullable=True),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "source", "external_id", name="uq_mtg_snapshot_source_external"),
    )
    op.create_index("ix_mtg_source_snapshots_game", "mtg_source_snapshots", ["game_id"], unique=False)
    op.create_index("ix_mtg_source_snapshots_updated", "mtg_source_snapshots", ["source_updated_at"], unique=False)

    op.create_table(
        "mtg_catalog_state",
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("active_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["active_snapshot_id"], ["mtg_source_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("game_id"),
    )

    op.create_table(
        "mtg_source_prints",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("scryfall_id", sa.String(length=64), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("set_id", sa.Integer(), nullable=False),
        sa.Column("collector_number", sa.String(length=50), nullable=False),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("rarity", sa.String(length=100), nullable=True),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("artist", sa.String(length=255), nullable=True),
        sa.Column("illustration_id", sa.String(length=64), nullable=True),
        sa.Column("frame", sa.String(length=32), nullable=True),
        sa.Column("border_color", sa.String(length=32), nullable=True),
        sa.Column("promo", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("image_url", sa.Text(), nullable=True),
        sa.Column("attributes_json", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["set_id"], ["sets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["mtg_source_snapshots.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scryfall_id", name="uq_mtg_source_print_scryfall"),
    )
    op.create_index("ix_mtg_source_prints_snapshot", "mtg_source_prints", ["snapshot_id"], unique=False)
    op.create_index("ix_mtg_source_prints_card", "mtg_source_prints", ["card_id"], unique=False)
    op.create_index("ix_mtg_source_prints_set", "mtg_source_prints", ["set_id"], unique=False)
    op.create_index("ix_mtg_source_prints_collector", "mtg_source_prints", ["collector_number"], unique=False)
    op.create_index("ix_mtg_source_prints_language", "mtg_source_prints", ["language"], unique=False)
    op.create_index(
        "ix_mtg_source_prints_set_collector_language",
        "mtg_source_prints",
        ["set_id", "collector_number", "language"],
        unique=False,
    )

    op.add_column("prints", sa.Column("mtg_source_print_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_prints_mtg_source_print_id",
        "prints",
        "mtg_source_prints",
        ["mtg_source_print_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_prints_mtg_source",
        "prints",
        ["mtg_source_print_id"],
        unique=False,
        postgresql_where=sa.text("mtg_source_print_id IS NOT NULL"),
    )
    op.create_index(
        "uq_prints_mtg_source_variant",
        "prints",
        ["mtg_source_print_id", "variant"],
        unique=True,
        postgresql_where=sa.text("mtg_source_print_id IS NOT NULL"),
    )


def downgrade() -> None:
    # Revision 26 introduces only MTG V2 exact rows. Removing them before the
    # SourcePrint FK/column makes downgrade deterministic while leaving every
    # non-MTG Print untouched. A production downgrade after market/economic rows
    # exist must first remap/remove those dependencies explicitly.
    op.execute("DELETE FROM prints WHERE mtg_source_print_id IS NOT NULL")
    op.drop_index("uq_prints_mtg_source_variant", table_name="prints")
    op.drop_index("ix_prints_mtg_source", table_name="prints")
    op.drop_constraint("fk_prints_mtg_source_print_id", "prints", type_="foreignkey")
    op.drop_column("prints", "mtg_source_print_id")

    op.drop_index("ix_mtg_source_prints_set_collector_language", table_name="mtg_source_prints")
    op.drop_index("ix_mtg_source_prints_language", table_name="mtg_source_prints")
    op.drop_index("ix_mtg_source_prints_collector", table_name="mtg_source_prints")
    op.drop_index("ix_mtg_source_prints_set", table_name="mtg_source_prints")
    op.drop_index("ix_mtg_source_prints_card", table_name="mtg_source_prints")
    op.drop_index("ix_mtg_source_prints_snapshot", table_name="mtg_source_prints")
    op.drop_table("mtg_source_prints")
    op.drop_table("mtg_catalog_state")
    op.drop_index("ix_mtg_source_snapshots_updated", table_name="mtg_source_snapshots")
    op.drop_index("ix_mtg_source_snapshots_game", table_name="mtg_source_snapshots")
    op.drop_table("mtg_source_snapshots")
