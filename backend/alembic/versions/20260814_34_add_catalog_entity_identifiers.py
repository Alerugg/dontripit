"""add language-scoped card and set source identifiers

Revision ID: 20260814_34
Revises: 20260814_33
Create Date: 2026-08-14 19:31:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_34"
down_revision: Union[str, None] = "20260814_33"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "set_identifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "set_id",
            sa.Integer(),
            sa.ForeignKey("sets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source", "external_id", name="uq_set_identifier_source_external"
        ),
        sa.UniqueConstraint("set_id", "source", name="uq_set_identifier_set_source"),
    )
    op.create_index("ix_set_identifiers_set_id", "set_identifiers", ["set_id"])
    op.create_index("ix_set_identifiers_source", "set_identifiers", ["source"])
    op.create_index(
        "ix_set_identifiers_external_id", "set_identifiers", ["external_id"]
    )

    op.create_table(
        "card_identifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "card_id",
            sa.Integer(),
            sa.ForeignKey("cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "source", "external_id", name="uq_card_identifier_source_external"
        ),
        sa.UniqueConstraint("card_id", "source", name="uq_card_identifier_card_source"),
    )
    op.create_index("ix_card_identifiers_card_id", "card_identifiers", ["card_id"])
    op.create_index("ix_card_identifiers_source", "card_identifiers", ["source"])
    op.create_index(
        "ix_card_identifiers_external_id", "card_identifiers", ["external_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_card_identifiers_external_id", table_name="card_identifiers")
    op.drop_index("ix_card_identifiers_source", table_name="card_identifiers")
    op.drop_index("ix_card_identifiers_card_id", table_name="card_identifiers")
    op.drop_table("card_identifiers")

    op.drop_index("ix_set_identifiers_external_id", table_name="set_identifiers")
    op.drop_index("ix_set_identifiers_source", table_name="set_identifiers")
    op.drop_index("ix_set_identifiers_set_id", table_name="set_identifiers")
    op.drop_table("set_identifiers")
