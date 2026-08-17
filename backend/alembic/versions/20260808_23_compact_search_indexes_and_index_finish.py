"""compact unused Search V2 indexes and index Pokémon finish

Revision ID: 20260808_23
Revises: 20260808_22
Create Date: 2026-08-08 14:08:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260808_23"
down_revision: Union[str, None] = "20260808_22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DROPPED_GIN_INDEXES = (
    # Current Search V2 SQL extracts scalar/array values explicitly and does not
    # use JSONB containment operators. Storage audit on 2026-08-08 recorded zero
    # scans for all four GIN indexes below, while they consumed ~45 MB combined.
    "ix_card_attributes_attributes_gin",
    "ix_print_attributes_attributes_gin",
    "ix_card_search_profiles_attributes_gin",
    "ix_print_search_profiles_attributes_gin",
)


def upgrade() -> None:
    for index_name in DROPPED_GIN_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {index_name}")

    # `finish` is a very selective physical-variant dimension for many values,
    # but `holo` is also common enough that scanning wide JSONB rows is expensive.
    # A partial expression index supports the exact predicate used by Advanced
    # Search without materializing another copy of the attribute.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_print_search_profiles_game_finish_lower
        ON print_search_profiles (
          game_id,
          lower(COALESCE(attributes_json ->> 'finish', ''))
        )
        WHERE COALESCE(attributes_json ->> 'finish', '') <> ''
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_game_finish_lower")

    # Recreate the original indexes exactly as introduced by revisions 19/20.
    # This downgrade is intentionally expensive; normal forward operation keeps
    # rich JSONB canonical while indexing only query-proven access paths.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_attributes_attributes_gin "
        "ON card_attributes USING gin (attributes_json)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_print_attributes_attributes_gin "
        "ON print_attributes USING gin (attributes_json)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_card_search_profiles_attributes_gin "
        "ON card_search_profiles USING gin (attributes_json)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_print_search_profiles_attributes_gin "
        "ON print_search_profiles USING gin (attributes_json)"
    )
