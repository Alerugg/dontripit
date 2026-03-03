"""ingest_engine_pro_tables

Revision ID: 20260303_09
Revises: 20260303_08
Create Date: 2026-03-03 02:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260303_09"
down_revision: Union[str, None] = "20260303_08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.alter_column("source_sync_state", "cursor_json", nullable=True, server_default=None)

    op.create_table(
        "field_provenance",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=True),
        sa.Column("value_json", _json_type(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_type", "entity_id", "field_name", "source", name="uq_field_provenance"),
    )
    op.create_index(op.f("ix_field_provenance_entity_type"), "field_provenance", ["entity_type"], unique=False)
    op.create_index(op.f("ix_field_provenance_entity_id"), "field_provenance", ["entity_id"], unique=False)
    op.create_index(op.f("ix_field_provenance_field_name"), "field_provenance", ["field_name"], unique=False)
    op.create_index(op.f("ix_field_provenance_source"), "field_provenance", ["source"], unique=False)

    op.execute(
        """
        INSERT INTO field_provenance (entity_type, entity_id, field_name, source, value_text, updated_at)
        SELECT 'print', print_id, field_name, source, value_text, updated_at
        FROM print_field_provenance
        """
    )

    op.execute("DROP VIEW IF EXISTS search_documents")
    op.create_table(
        "search_documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("object_id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subtitle", sa.Text(), nullable=True),
        sa.Column("tsv", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("doc_type", "object_id", name="uq_search_documents_doc_object"),
    )
    op.create_index(op.f("ix_search_documents_doc_type"), "search_documents", ["doc_type"], unique=False)
    op.create_index(op.f("ix_search_documents_game_id"), "search_documents", ["game_id"], unique=False)

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("CREATE INDEX IF NOT EXISTS ix_search_documents_tsv ON search_documents USING GIN (to_tsvector('simple', coalesce(tsv, '')))" )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_search_documents_tsv")
    op.drop_index(op.f("ix_search_documents_game_id"), table_name="search_documents")
    op.drop_index(op.f("ix_search_documents_doc_type"), table_name="search_documents")
    op.drop_table("search_documents")

    op.drop_index(op.f("ix_field_provenance_source"), table_name="field_provenance")
    op.drop_index(op.f("ix_field_provenance_field_name"), table_name="field_provenance")
    op.drop_index(op.f("ix_field_provenance_entity_id"), table_name="field_provenance")
    op.drop_index(op.f("ix_field_provenance_entity_type"), table_name="field_provenance")
    op.drop_table("field_provenance")

    op.alter_column("source_sync_state", "cursor_json", nullable=False, server_default=sa.text("'{}'"))
