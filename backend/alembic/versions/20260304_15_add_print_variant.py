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
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    column_names = {column["name"] for column in inspector.get_columns("prints")}

    if "variant" not in column_names:
        op.add_column("prints", sa.Column("variant", sa.String(length=100), nullable=True, server_default="default"))

    op.execute("UPDATE prints SET variant = 'default' WHERE variant IS NULL OR trim(variant) = ''")
    op.alter_column("prints", "variant", existing_type=sa.String(length=100), nullable=False, server_default="default")

    unique_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("prints") if constraint.get("name")}
    if "uq_prints_set_number_language_is_foil" in unique_constraints:
        op.drop_constraint("uq_prints_set_number_language_is_foil", "prints", type_="unique")
    if "uq_prints_set_number_language_is_foil_variant" not in unique_constraints:
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
