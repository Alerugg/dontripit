"""add Pokémon Search V2 numeric expression indexes

Revision ID: 20260808_22
Revises: 20260808_21
Create Date: 2026-08-08 13:55:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260808_22"
down_revision: Union[str, None] = "20260808_21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # JSONB GIN is excellent for containment, but integer ranges such as HP and
    # release year need typed expression indexes. The partial predicate protects
    # historical/non-Pokémon rows whose JSON value is absent or non-numeric.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_print_search_profiles_game_hp_int
        ON print_search_profiles (game_id, ((attributes_json ->> 'hp')::integer))
        WHERE (attributes_json ->> 'hp') ~ '^[0-9]+$'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_print_search_profiles_game_release_year_int
        ON print_search_profiles (game_id, ((attributes_json ->> 'release_year')::integer))
        WHERE (attributes_json ->> 'release_year') ~ '^[0-9]+$'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_game_release_year_int")
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_game_hp_int")
