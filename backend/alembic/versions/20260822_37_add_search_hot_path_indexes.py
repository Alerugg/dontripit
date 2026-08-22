"""add search hot-path indexes

Revision ID: 20260822_37
Revises: 20260815_36
Create Date: 2026-08-22

The public Search V2 exact-ID path resolves MTG prints by game + set code +
collector number. Keeping those columns in one compact btree avoids intersecting
three independent indexes on the latency-sensitive request path.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260822_37"
down_revision: Union[str, None] = "20260815_36"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "ix_print_search_profiles_game_set_collector"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON print_search_profiles (
          game_id,
          normalized_set_code,
          normalized_collector_number
        )
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
