"""add_sources_and_search

Revision ID: 20260302_03
Revises: 20260302_02
Create Date: 2026-03-02 01:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260302_03"
down_revision: Union[str, None] = "20260302_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_sources_name", "sources", ["name"], unique=True)

    op.create_table(
        "source_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("raw_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source_id", "checksum", name="uq_source_records_source_checksum"),
    )
    op.create_index("ix_source_records_source_id", "source_records", ["source_id"], unique=False)
    op.create_index("ix_source_records_checksum", "source_records", ["checksum"], unique=False)

    op.execute(
        """
        INSERT INTO sources (name, description)
        VALUES ('fixture_local', 'Local JSON fixture connector')
        ON CONFLICT (name) DO NOTHING
        """
    )

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_cards_name_tsv ON cards USING GIN (to_tsvector('simple', coalesce(name, '')))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_sets_name_code_tsv ON sets USING GIN (to_tsvector('simple', coalesce(name, '') || ' ' || coalesce(code, '')))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_prints_collector_tsv ON prints USING GIN (to_tsvector('simple', coalesce(collector_number, '')))"
    )

    op.execute(
        """
        CREATE VIEW search_documents AS
        SELECT
            'card'::text AS doc_type,
            c.id AS object_id,
            c.game_id AS game_id,
            c.name::text AS title,
            NULL::text AS subtitle,
            to_tsvector('simple', coalesce(c.name, '')) AS tsv
        FROM cards c
        UNION ALL
        SELECT
            'set'::text AS doc_type,
            s.id AS object_id,
            s.game_id AS game_id,
            s.name::text AS title,
            s.code::text AS subtitle,
            to_tsvector('simple', coalesce(s.name, '') || ' ' || coalesce(s.code, '')) AS tsv
        FROM sets s
        UNION ALL
        SELECT
            'print'::text AS doc_type,
            p.id AS object_id,
            c.game_id AS game_id,
            c.name::text AS title,
            (s.code || ' #' || p.collector_number)::text AS subtitle,
            to_tsvector('simple', coalesce(c.name, '') || ' ' || coalesce(s.name, '') || ' ' || coalesce(s.code, '') || ' ' || coalesce(p.collector_number, '')) AS tsv
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS search_documents")
    op.execute("DROP INDEX IF EXISTS ix_prints_collector_tsv")
    op.execute("DROP INDEX IF EXISTS ix_sets_name_code_tsv")
    op.execute("DROP INDEX IF EXISTS ix_cards_name_tsv")

    op.drop_index("ix_source_records_checksum", table_name="source_records")
    op.drop_index("ix_source_records_source_id", table_name="source_records")
    op.drop_table("source_records")

    op.drop_index("ix_sources_name", table_name="sources")
    op.drop_table("sources")
