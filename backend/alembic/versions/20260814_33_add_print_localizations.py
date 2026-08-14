"""add print localizations

Revision ID: 20260814_33
Revises: 20260810_32
Create Date: 2026-08-14 19:05:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260814_33"
down_revision: Union[str, None] = "20260810_32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "print_localizations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "print_id",
            sa.Integer(),
            sa.ForeignKey("prints.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("language", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("card_name", sa.String(length=255), nullable=True),
        sa.Column("set_name", sa.String(length=255), nullable=True),
        sa.Column(
            "details_json",
            postgresql.JSONB(astext_type=sa.Text()).with_variant(sa.JSON(), "sqlite"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "print_id",
            "language",
            "source",
            name="uq_print_localizations_print_language_source",
        ),
    )
    op.create_index(
        "ix_print_localizations_print_id",
        "print_localizations",
        ["print_id"],
        unique=False,
    )
    op.create_index(
        "ix_print_localizations_language",
        "print_localizations",
        ["language"],
        unique=False,
    )
    op.create_index(
        "ix_print_localizations_source",
        "print_localizations",
        ["source"],
        unique=False,
    )
    op.create_index(
        "ix_print_localizations_external_id",
        "print_localizations",
        ["external_id"],
        unique=False,
    )
    op.create_index(
        "ix_print_localizations_card_name",
        "print_localizations",
        ["card_name"],
        unique=False,
    )
    op.create_index(
        "ix_print_localizations_language_card_name",
        "print_localizations",
        ["language", "card_name"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_print_localizations_language_card_name", table_name="print_localizations")
    op.drop_index("ix_print_localizations_card_name", table_name="print_localizations")
    op.drop_index("ix_print_localizations_external_id", table_name="print_localizations")
    op.drop_index("ix_print_localizations_source", table_name="print_localizations")
    op.drop_index("ix_print_localizations_language", table_name="print_localizations")
    op.drop_index("ix_print_localizations_print_id", table_name="print_localizations")
    op.drop_table("print_localizations")
