"""add pokemon numeric Search V2 indexes

Revision ID: 20260810_30
Revises: 20260810_29
Create Date: 2026-08-10 10:45:00.000000
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260810_30"
down_revision: Union[str, None] = "20260810_29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Advanced Pokémon ranges previously cast JSON text for every physical
    # print. Match the production predicate exactly so PostgreSQL can answer
    # HP and release-year ranges from a small game-scoped btree.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_print_search_profiles_game_hp_int
        ON print_search_profiles (
          game_id,
          ((NULLIF(attributes_json ->> 'hp', ''))::integer)
        )
        WHERE COALESCE(attributes_json ->> 'hp', '') ~ '^[0-9]+$'
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_print_search_profiles_game_release_year_int
        ON print_search_profiles (
          game_id,
          ((NULLIF(attributes_json ->> 'release_year', ''))::integer)
        )
        WHERE COALESCE(attributes_json ->> 'release_year', '') ~ '^[0-9]+$'
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_game_release_year_int")
    op.execute("DROP INDEX IF EXISTS ix_print_search_profiles_game_hp_int")
