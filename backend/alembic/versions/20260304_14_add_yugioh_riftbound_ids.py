"""add yugioh and riftbound external ids

Revision ID: 20260304_14
Revises: 20260303_13
Create Date: 2026-03-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20260304_14"
down_revision = "20260303_13"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sets", sa.Column("yugioh_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_sets_yugioh_id"), "sets", ["yugioh_id"], unique=False)
    op.create_unique_constraint("uq_sets_game_yugioh", "sets", ["game_id", "yugioh_id"])

    op.add_column("cards", sa.Column("yugoprodeck_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_cards_yugoprodeck_id"), "cards", ["yugoprodeck_id"], unique=False)
    op.create_unique_constraint("uq_cards_game_yugoprodeck", "cards", ["game_id", "yugoprodeck_id"])

    op.add_column("prints", sa.Column("yugioh_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_prints_yugioh_id"), "prints", ["yugioh_id"], unique=False)
    op.create_unique_constraint("uq_prints_yugioh_id", "prints", ["yugioh_id"])

    op.add_column("sets", sa.Column("riftbound_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_sets_riftbound_id"), "sets", ["riftbound_id"], unique=False)
    op.create_unique_constraint("uq_sets_game_riftbound", "sets", ["game_id", "riftbound_id"])

    op.add_column("cards", sa.Column("riftbound_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_cards_riftbound_id"), "cards", ["riftbound_id"], unique=False)
    op.create_unique_constraint("uq_cards_game_riftbound", "cards", ["game_id", "riftbound_id"])

    op.add_column("prints", sa.Column("riftbound_id", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_prints_riftbound_id"), "prints", ["riftbound_id"], unique=False)
    op.create_unique_constraint("uq_prints_riftbound_id", "prints", ["riftbound_id"])


def downgrade() -> None:
    op.drop_constraint("uq_prints_riftbound_id", "prints", type_="unique")
    op.drop_index(op.f("ix_prints_riftbound_id"), table_name="prints")
    op.drop_column("prints", "riftbound_id")

    op.drop_constraint("uq_cards_game_riftbound", "cards", type_="unique")
    op.drop_index(op.f("ix_cards_riftbound_id"), table_name="cards")
    op.drop_column("cards", "riftbound_id")

    op.drop_constraint("uq_sets_game_riftbound", "sets", type_="unique")
    op.drop_index(op.f("ix_sets_riftbound_id"), table_name="sets")
    op.drop_column("sets", "riftbound_id")

    op.drop_constraint("uq_prints_yugioh_id", "prints", type_="unique")
    op.drop_index(op.f("ix_prints_yugioh_id"), table_name="prints")
    op.drop_column("prints", "yugioh_id")

    op.drop_constraint("uq_cards_game_yugoprodeck", "cards", type_="unique")
    op.drop_index(op.f("ix_cards_yugoprodeck_id"), table_name="cards")
    op.drop_column("cards", "yugoprodeck_id")

    op.drop_constraint("uq_sets_game_yugioh", "sets", type_="unique")
    op.drop_index(op.f("ix_sets_yugioh_id"), table_name="sets")
    op.drop_column("sets", "yugioh_id")
