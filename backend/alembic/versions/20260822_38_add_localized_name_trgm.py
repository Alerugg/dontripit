"""add localized card-name trigram index

Revision ID: 20260822_38
Revises: 20260822_37
Create Date: 2026-08-22

Yu-Gi-Oh fuzzy search preserves Spanish/Japanese physical localization names.
The hot path performs a contains lookup over lower(print_localizations.card_name),
so give PostgreSQL a matching functional pg_trgm index rather than scanning the
localized catalog on every miss/typo query.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "20260822_38"
down_revision: Union[str, None] = "20260822_37"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


INDEX_NAME = "ix_print_localizations_card_name_lower_trgm"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {INDEX_NAME}
        ON print_localizations
        USING gin (lower(card_name) gin_trgm_ops)
        WHERE card_name IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
