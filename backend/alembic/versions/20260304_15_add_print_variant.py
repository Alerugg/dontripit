"""add_print_variant

Revision ID: 20260304_15
Revises: 20260304_14
Create Date: 2026-03-04 15:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260304_15"
down_revision: Union[str, None] = "20260304_14"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("prints", sa.Column("variant", sa.String(length=100), nullable=False, server_default="default"))
    op.drop_constraint("uq_prints_set_number_language_is_foil", "prints", type_="unique")
    op.create_unique_constraint(
        "uq_prints_set_number_language_is_foil_variant",
        "prints",
        ["set_id", "collector_number", "language", "is_foil", "variant"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_prints_set_number_language_is_foil_variant", "prints", type_="unique")
    op.create_unique_constraint(
        "uq_prints_set_number_language_is_foil",
        "prints",
        ["set_id", "collector_number", "language", "is_foil"],
    )
    op.drop_column("prints", "variant")
