"""scheduler_quality_tables

Revision ID: 20260302_04
Revises: 20260302_03
Create Date: 2026-03-02 02:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260302_04"
down_revision: Union[str, None] = "20260302_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "source_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor_json", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_source_sync_state_source_id", "source_sync_state", ["source_id"], unique=True)

    op.create_table(
        "ingest_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_id", sa.Integer(), sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("counts_json", _json_type(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("error_summary", sa.Text(), nullable=True),
    )
    op.create_index("ix_ingest_runs_source_id", "ingest_runs", ["source_id"], unique=False)
    op.create_index("ix_ingest_runs_status", "ingest_runs", ["status"], unique=False)

    op.create_table(
        "print_field_provenance",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id"), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("print_id", "field_name", "source", name="uq_print_field_provenance"),
    )
    op.create_index("ix_print_field_provenance_print_id", "print_field_provenance", ["print_id"], unique=False)
    op.create_index("ix_print_field_provenance_field_name", "print_field_provenance", ["field_name"], unique=False)
    op.create_index("ix_print_field_provenance_source", "print_field_provenance", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_print_field_provenance_source", table_name="print_field_provenance")
    op.drop_index("ix_print_field_provenance_field_name", table_name="print_field_provenance")
    op.drop_index("ix_print_field_provenance_print_id", table_name="print_field_provenance")
    op.drop_table("print_field_provenance")

    op.drop_index("ix_ingest_runs_status", table_name="ingest_runs")
    op.drop_index("ix_ingest_runs_source_id", table_name="ingest_runs")
    op.drop_table("ingest_runs")

    op.drop_index("ix_source_sync_state_source_id", table_name="source_sync_state")
    op.drop_table("source_sync_state")
