"""add interactive card-name prefix index

Revision ID: 20260823_39
Revises: 20260822_38
Create Date: 2026-08-23

Interactive search uses trigram matching for 3+ character contains queries, but
1-2 character typeahead deliberately stays prefix-only. The existing exact-name
btree is not guaranteed to serve LIKE 'prefix%' efficiently under non-C
collations, so add a compact pattern_ops companion scoped by game_id.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260823_39"
down_revision: Union[str, None] = "20260822_38"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "ix_card_search_profiles_game_name_pattern"


def upgrade() -> None:
    # Avoid blocking daily ingest writers while PostgreSQL builds the index.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME}
            ON card_search_profiles (game_id, normalized_name text_pattern_ops)
            """
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")
