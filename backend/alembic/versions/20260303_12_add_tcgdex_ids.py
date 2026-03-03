"""add_tcgdex_ids

Revision ID: 20260303_12
Revises: 20260303_11
Create Date: 2026-03-03 05:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260303_12"
down_revision: Union[str, None] = "20260303_11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("sets", sa.Column("tcgdex_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_sets_tcgdex_id"), "sets", ["tcgdex_id"], unique=False)
    op.create_unique_constraint("uq_sets_game_tcgdex", "sets", ["game_id", "tcgdex_id"])

    op.add_column("cards", sa.Column("tcgdex_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_cards_tcgdex_id"), "cards", ["tcgdex_id"], unique=False)
    op.create_unique_constraint("uq_cards_game_tcgdex", "cards", ["game_id", "tcgdex_id"])

    op.add_column("prints", sa.Column("tcgdex_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_prints_tcgdex_id"), "prints", ["tcgdex_id"], unique=False)
    op.create_unique_constraint("uq_prints_tcgdex_id", "prints", ["tcgdex_id"])


def downgrade() -> None:
    op.drop_constraint("uq_prints_tcgdex_id", "prints", type_="unique")
    op.drop_index(op.f("ix_prints_tcgdex_id"), table_name="prints")
    op.drop_column("prints", "tcgdex_id")

    op.drop_constraint("uq_cards_game_tcgdex", "cards", type_="unique")
    op.drop_index(op.f("ix_cards_tcgdex_id"), table_name="cards")
    op.drop_column("cards", "tcgdex_id")

    op.drop_constraint("uq_sets_game_tcgdex", "sets", type_="unique")
    op.drop_index(op.f("ix_sets_tcgdex_id"), table_name="sets")
    op.drop_column("sets", "tcgdex_id")
