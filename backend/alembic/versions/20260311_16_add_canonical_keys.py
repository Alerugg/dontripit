"""add_canonical_keys

Revision ID: 20260311_16
Revises: 20260304_15
Create Date: 2026-03-11 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260311_16"
down_revision: Union[str, None] = "20260304_15"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    card_columns = {column["name"] for column in inspector.get_columns("cards")}
    if "card_key" not in card_columns:
        op.add_column("cards", sa.Column("card_key", sa.String(length=255), nullable=True))
    card_indexes = {index["name"] for index in inspector.get_indexes("cards")}
    if "ix_cards_card_key" not in card_indexes:
        op.create_index("ix_cards_card_key", "cards", ["card_key"], unique=False)

    print_columns = {column["name"] for column in inspector.get_columns("prints")}
    if "print_key" not in print_columns:
        op.add_column("prints", sa.Column("print_key", sa.String(length=512), nullable=True))
    print_indexes = {index["name"] for index in inspector.get_indexes("prints")}
    if "ix_prints_print_key" not in print_indexes:
        op.create_index("ix_prints_print_key", "prints", ["print_key"], unique=False)

    print_constraints = {constraint["name"] for constraint in inspector.get_unique_constraints("prints") if constraint.get("name")}
    if "uq_prints_print_key" not in print_constraints:
        op.create_unique_constraint("uq_prints_print_key", "prints", ["print_key"])


def downgrade() -> None:
    op.drop_constraint("uq_prints_print_key", "prints", type_="unique")
    op.drop_index("ix_prints_print_key", table_name="prints")
    op.drop_column("prints", "print_key")

    op.drop_index("ix_cards_card_key", table_name="cards")
    op.drop_column("cards", "card_key")
