"""add prices skeleton tables

Revision ID: 20260303_13
Revises: 20260303_12
Create Date: 2026-03-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260303_13"
down_revision = "20260303_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("price_sources", sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"))

    op.create_table(
        "prices",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("game_id", sa.Integer(), nullable=False),
        sa.Column("print_id", sa.Integer(), nullable=True),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"]),
        sa.ForeignKeyConstraint(["game_id"], ["games.id"]),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"]),
        sa.ForeignKeyConstraint(["source_id"], ["price_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_prices_game_id", "prices", ["game_id"])
    op.create_index("ix_prices_print_id", "prices", ["print_id"])
    op.create_index("ix_prices_card_id", "prices", ["card_id"])
    op.create_index("ix_prices_source_id", "prices", ["source_id"])
    op.create_index("ix_prices_captured_at", "prices", ["captured_at"])
    op.create_index("ix_prices_source_game_captured", "prices", ["source_id", "game_id", "captured_at"])
    op.create_index("ix_prices_print_source_captured", "prices", ["print_id", "source_id", "captured_at"])


def downgrade() -> None:
    op.drop_index("ix_prices_print_source_captured", table_name="prices")
    op.drop_index("ix_prices_source_game_captured", table_name="prices")
    op.drop_index("ix_prices_captured_at", table_name="prices")
    op.drop_index("ix_prices_source_id", table_name="prices")
    op.drop_index("ix_prices_card_id", table_name="prices")
    op.drop_index("ix_prices_print_id", table_name="prices")
    op.drop_index("ix_prices_game_id", table_name="prices")
    op.drop_table("prices")
    op.drop_column("price_sources", "currency")
