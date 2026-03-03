"""add_scryfall_ids

Revision ID: 20260303_11
Revises: 20260303_10
Create Date: 2026-03-03 04:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260303_11"
down_revision: Union[str, None] = "20260303_10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cards", sa.Column("oracle_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_cards_oracle_id"), "cards", ["oracle_id"], unique=False)
    op.create_unique_constraint("uq_cards_game_oracle", "cards", ["game_id", "oracle_id"])

    op.add_column("prints", sa.Column("scryfall_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_prints_scryfall_id"), "prints", ["scryfall_id"], unique=False)
    op.create_unique_constraint("uq_prints_scryfall_id", "prints", ["scryfall_id"])


def downgrade() -> None:
    op.drop_constraint("uq_prints_scryfall_id", "prints", type_="unique")
    op.drop_index(op.f("ix_prints_scryfall_id"), table_name="prints")
    op.drop_column("prints", "scryfall_id")

    op.drop_constraint("uq_cards_game_oracle", "cards", type_="unique")
    op.drop_index(op.f("ix_cards_oracle_id"), table_name="cards")
    op.drop_column("cards", "oracle_id")
