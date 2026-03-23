"""relax_card_name_uniqueness_for_mtg

Revision ID: 20260323_17
Revises: 20260311_16
Create Date: 2026-03-23 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260323_17"
down_revision: Union[str, None] = "20260311_16"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CARD_IDENTITY_EXPR = "coalesce(oracle_id, tcgdex_id, yugoprodeck_id, riftbound_id, card_key, name)"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    card_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("cards")
        if constraint.get("name")
    }
    if "uq_cards_game_name" in card_constraints:
        op.drop_constraint("uq_cards_game_name", "cards", type_="unique")

    card_indexes = {index["name"] for index in inspector.get_indexes("cards")}
    if "uq_cards_game_identity" not in card_indexes:
        op.create_index(
            "uq_cards_game_identity",
            "cards",
            ["game_id", sa.text(CARD_IDENTITY_EXPR)],
            unique=True,
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    card_indexes = {index["name"] for index in inspector.get_indexes("cards")}
    if "uq_cards_game_identity" in card_indexes:
        op.drop_index("uq_cards_game_identity", table_name="cards")

    card_constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("cards")
        if constraint.get("name")
    }
    if "uq_cards_game_name" not in card_constraints:
        op.create_unique_constraint("uq_cards_game_name", "cards", ["game_id", "name"])
