"""add_price_snapshots

Revision ID: 20260303_10
Revises: 20260303_09
Create Date: 2026-03-03 03:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260303_10"
down_revision: Union[str, None] = "20260303_09"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _json_type():
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def upgrade() -> None:
    op.create_table(
        "price_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )

    op.create_table(
        "price_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_low", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_mid", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_high", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_market", sa.Numeric(12, 2), nullable=True),
        sa.Column("price_last", sa.Numeric(12, 2), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_json", _json_type(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["price_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "source_id",
            "currency",
            "as_of",
            name="uq_price_snapshot_identity",
        ),
    )
    op.create_index(op.f("ix_price_snapshots_entity_type"), "price_snapshots", ["entity_type"], unique=False)
    op.create_index(op.f("ix_price_snapshots_entity_id"), "price_snapshots", ["entity_id"], unique=False)
    op.create_index(op.f("ix_price_snapshots_source_id"), "price_snapshots", ["source_id"], unique=False)
    op.create_index(op.f("ix_price_snapshots_currency"), "price_snapshots", ["currency"], unique=False)
    op.create_index(op.f("ix_price_snapshots_as_of"), "price_snapshots", ["as_of"], unique=False)

    op.create_table(
        "price_daily_ohlc",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(12, 2), nullable=False),
        sa.Column("high", sa.Numeric(12, 2), nullable=False),
        sa.Column("low", sa.Numeric(12, 2), nullable=False),
        sa.Column("close", sa.Numeric(12, 2), nullable=False),
        sa.Column("volume", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["source_id"], ["price_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type",
            "entity_id",
            "source_id",
            "currency",
            "day",
            name="uq_price_daily_ohlc_identity",
        ),
    )
    op.create_index(op.f("ix_price_daily_ohlc_day"), "price_daily_ohlc", ["day"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_price_daily_ohlc_day"), table_name="price_daily_ohlc")
    op.drop_table("price_daily_ohlc")

    op.drop_index(op.f("ix_price_snapshots_as_of"), table_name="price_snapshots")
    op.drop_index(op.f("ix_price_snapshots_currency"), table_name="price_snapshots")
    op.drop_index(op.f("ix_price_snapshots_source_id"), table_name="price_snapshots")
    op.drop_index(op.f("ix_price_snapshots_entity_id"), table_name="price_snapshots")
    op.drop_index(op.f("ix_price_snapshots_entity_type"), table_name="price_snapshots")
    op.drop_table("price_snapshots")

    op.drop_table("price_sources")
