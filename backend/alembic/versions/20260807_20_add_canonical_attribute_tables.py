"""add canonical game-specific attribute tables

Revision ID: 20260807_20
Revises: 20260807_19
Create Date: 2026-08-07 22:45:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260807_20"
down_revision: Union[str, None] = "20260807_19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "card_attributes",
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("attributes_json", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["card_id"], ["cards.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("card_id"),
    )
    op.create_index(
        "ix_card_attributes_attributes_gin",
        "card_attributes",
        ["attributes_json"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index("ix_card_attributes_source", "card_attributes", ["source"], unique=False)

    op.create_table(
        "print_attributes",
        sa.Column("print_id", sa.Integer(), nullable=False),
        sa.Column("attributes_json", jsonb, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_version", sa.String(length=100), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["print_id"], ["prints.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("print_id"),
    )
    op.create_index(
        "ix_print_attributes_attributes_gin",
        "print_attributes",
        ["attributes_json"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_index("ix_print_attributes_source", "print_attributes", ["source"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_print_attributes_source", table_name="print_attributes")
    op.drop_index("ix_print_attributes_attributes_gin", table_name="print_attributes")
    op.drop_table("print_attributes")
    op.drop_index("ix_card_attributes_source", table_name="card_attributes")
    op.drop_index("ix_card_attributes_attributes_gin", table_name="card_attributes")
    op.drop_table("card_attributes")
