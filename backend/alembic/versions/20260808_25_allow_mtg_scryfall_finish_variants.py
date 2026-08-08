"""allow one Scryfall object to expose multiple exact MTG finishes

Revision ID: 20260808_25
Revises: 20260808_24
Create Date: 2026-08-08 20:24:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_25"
down_revision: Union[str, None] = "20260808_24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A Scryfall card object is a source-print object. One object can currently
    # advertise several physical finishes (nonfoil / foil / etched), so the
    # source id itself cannot remain globally unique on an exact Print row.
    op.drop_constraint("uq_prints_scryfall_id", "prints", type_="unique")

    # Keep source lookup indexed while making the exact upstream identity
    # Scryfall object + certified finish. `variant` is the exact finish for MTG
    # V2 and `print_key` remains the cross-TCG canonical physical identity.
    op.create_index(
        "uq_prints_scryfall_variant",
        "prints",
        ["scryfall_id", "variant"],
        unique=True,
        postgresql_where=sa.text("scryfall_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_prints_scryfall_variant", table_name="prints")
    # This downgrade is intentionally safety-first: once MTG V2 contains more
    # than one finish for a Scryfall object, PostgreSQL will refuse to recreate
    # the old global unique constraint rather than silently collapsing rows.
    op.create_unique_constraint("uq_prints_scryfall_id", "prints", ["scryfall_id"])
