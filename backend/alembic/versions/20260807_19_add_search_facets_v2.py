"""add_search_facets_v2

Revision ID: 20260807_19
Revises: 20260807_18
Create Date: 2026-08-07 18:38:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260807_19"
down_revision: Union[str, None] = "20260807_18"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL pg_trgm gives fast contains/fuzzy matching for human card names.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "card_search_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=True),
        sa.Column("keywords_json", sa.JSON(), nullable=True),
        sa.Column("attributes_json", sa.JSON(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_id"),
    )
    op.create_index("ix_card_search_profiles_card_id", "card_search_profiles", ["card_id"], unique=True)
    op.create_index("ix_card_search_profiles_game_id", "card_search_profiles", ["game_id"], unique=False)
    op.execute(
        "CREATE INDEX ix_card_search_profiles_name_trgm "
        "ON card_search_profiles USING gin (normalized_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_card_search_profiles_text_trgm "
        "ON card_search_profiles USING gin (search_text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_card_search_profiles_attributes_gin "
        "ON card_search_profiles USING gin (attributes_json)"
    )

    op.create_table(
        "print_search_profiles",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("print_id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("normalized_name", sa.Text(), nullable=False),
        sa.Column("normalized_set_code", sa.String(length=100), nullable=True),
        sa.Column("normalized_collector_number", sa.String(length=100), nullable=True),
        sa.Column("language", sa.String(length=16), nullable=True),
        sa.Column("rarity", sa.String(length=100), nullable=True),
        sa.Column("exact_variant", sa.String(length=100), nullable=True),
        sa.Column("variant_family", sa.String(length=100), nullable=True),
        sa.Column("release_names_json", sa.JSON(), nullable=True),
        sa.Column("aliases_json", sa.JSON(), nullable=True),
        sa.Column("keywords_json", sa.JSON(), nullable=True),
        sa.Column("attributes_json", sa.JSON(), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("print_id"),
    )
    for name, column in [
        ("ix_print_search_profiles_print_id", "print_id"),
        ("ix_print_search_profiles_card_id", "card_id"),
        ("ix_print_search_profiles_game_id", "game_id"),
        ("ix_print_search_profiles_normalized_set_code", "normalized_set_code"),
        ("ix_print_search_profiles_normalized_collector_number", "normalized_collector_number"),
        ("ix_print_search_profiles_language", "language"),
        ("ix_print_search_profiles_rarity", "rarity"),
        ("ix_print_search_profiles_exact_variant", "exact_variant"),
        ("ix_print_search_profiles_variant_family", "variant_family"),
    ]:
        op.create_index(name, "print_search_profiles", [column], unique=(column == "print_id"))
    op.execute(
        "CREATE INDEX ix_print_search_profiles_name_trgm "
        "ON print_search_profiles USING gin (normalized_name gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_print_search_profiles_text_trgm "
        "ON print_search_profiles USING gin (search_text gin_trgm_ops)"
    )
    op.execute(
        "CREATE INDEX ix_print_search_profiles_attributes_gin "
        "ON print_search_profiles USING gin (attributes_json)"
    )

    op.create_table(
        "facet_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.String(length=32), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("value_type", sa.String(length=32), nullable=False),
        sa.Column("ui_type", sa.String(length=32), nullable=False),
        sa.Column("group_name", sa.String(length=100), nullable=True),
        sa.Column("source_path", sa.String(length=255), nullable=False),
        sa.Column("multi_value", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("filterable", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sortable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("searchable", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("quick_filter", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("display_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("options_json", sa.JSON(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("game_id", "scope", "key", name="uq_facet_definition_game_scope_key"),
    )
    for column in ("game_id", "scope", "key", "group_name"):
        op.create_index(f"ix_facet_definitions_{column}", "facet_definitions", [column], unique=False)


def downgrade() -> None:
    for column in ("group_name", "key", "scope", "game_id"):
        op.drop_index(f"ix_facet_definitions_{column}", table_name="facet_definitions")
    op.drop_table("facet_definitions")

    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_attributes_gin")
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_text_trgm")
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_name_trgm")
    for name in (
        "ix_print_search_profiles_variant_family",
        "ix_print_search_profiles_exact_variant",
        "ix_print_search_profiles_rarity",
        "ix_print_search_profiles_language",
        "ix_print_search_profiles_normalized_collector_number",
        "ix_print_search_profiles_normalized_set_code",
        "ix_print_search_profiles_game_id",
        "ix_print_search_profiles_card_id",
        "ix_print_search_profiles_print_id",
    ):
        op.drop_index(name, table_name="print_search_profiles")
    op.drop_table("print_search_profiles")

    op.execute("DROP INDEX IF EXISTS ix_card_search_profiles_attributes_gin")
    op.execute("DROP INDEX IF EXISTS ix_card_search_profiles_text_trgm")
    op.execute("DROP INDEX IF EXISTS ix_card_search_profiles_name_trgm")
    op.drop_index("ix_card_search_profiles_game_id", table_name="card_search_profiles")
    op.drop_index("ix_card_search_profiles_card_id", table_name="card_search_profiles")
    op.drop_table("card_search_profiles")
