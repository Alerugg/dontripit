"""remove redundant indexes before lean Search V2 compaction

Revision ID: 20260808_24
Revises: 20260808_23
Create Date: 2026-08-08 14:18:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260808_24"
down_revision: Union[str, None] = "20260808_23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Each prints.* external-id column already has a single-column UNIQUE constraint,
# whose btree index supports the same equality lookups as these non-unique copies.
REDUNDANT_PRINT_INDEXES = (
    "ix_prints_scryfall_id",
    "ix_prints_tcgdex_id",
    "ix_prints_yugioh_id",
    "ix_prints_riftbound_id",
    "ix_prints_print_key",
)


def upgrade() -> None:
    for index_name in REDUNDANT_PRINT_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # Pokémon's production natural search is card-first. Exact/prefix/fuzzy name
    # matching uses normalized_name; Search V2 no longer needs a second 30+ MB
    # trigram index over the full duplicated card search document.
    op.execute("DROP INDEX IF EXISTS ix_card_search_profiles_text_trgm")

    # Small btree for the high-frequency exact-name path (Pikachu, Charizard,
    # Luffy-style direct searches in future game-specific card-first search).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_card_search_profiles_game_name_exact
        ON card_search_profiles (game_id, normalized_name)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_card_search_profiles_game_name_exact")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_search_profiles_text_trgm "
        "ON card_search_profiles USING gin (search_text gin_trgm_ops)"
    )

    # Recreate historical non-unique indexes only for a schema rollback. Forward
    # operation relies on the corresponding UNIQUE indexes.
    for column in ("scryfall_id", "tcgdex_id", "yugioh_id", "riftbound_id", "print_key"):
        op.create_index(f"ix_prints_{column}", "prints", [column], unique=False)
