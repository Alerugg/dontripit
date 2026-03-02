"""add_tcg_core_tables

Revision ID: 20260302_02
Revises: 20260302_01
Create Date: 2026-03-02 00:30:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260302_02"
down_revision: Union[str, None] = "20260302_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("release_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("game_id", "code", name="uq_sets_game_id_code"),
    )
    op.create_index("ix_sets_game_id", "sets", ["game_id"], unique=False)
    op.create_index("ix_sets_code", "sets", ["code"], unique=False)
    op.create_index("ix_sets_name", "sets", ["name"], unique=False)

    op.create_table(
        "cards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("game_id", sa.Integer(), sa.ForeignKey("games.id"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_cards_game_id", "cards", ["game_id"], unique=False)
    op.create_index("ix_cards_name", "cards", ["name"], unique=False)

    op.create_table(
        "prints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("card_id", sa.Integer(), sa.ForeignKey("cards.id"), nullable=False),
        sa.Column("set_id", sa.Integer(), sa.ForeignKey("sets.id"), nullable=False),
        sa.Column("collector_number", sa.String(length=50), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("rarity", sa.String(length=50), nullable=False),
        sa.Column("is_foil", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "set_id",
            "collector_number",
            "language",
            "is_foil",
            name="uq_prints_set_number_language_is_foil",
        ),
    )
    op.create_index("ix_prints_card_id", "prints", ["card_id"], unique=False)
    op.create_index("ix_prints_set_id", "prints", ["set_id"], unique=False)
    op.create_index("ix_prints_collector_number", "prints", ["collector_number"], unique=False)
    op.create_index("ix_prints_language", "prints", ["language"], unique=False)
    op.create_index("ix_prints_rarity", "prints", ["rarity"], unique=False)
    op.create_index("ix_prints_is_foil", "prints", ["is_foil"], unique=False)

    op.create_table(
        "print_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_print_images_print_id", "print_images", ["print_id"], unique=False)
    op.create_index("ix_print_images_is_primary", "print_images", ["is_primary"], unique=False)

    op.create_table(
        "print_identifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id"), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_print_identifiers_source_external_id"),
    )
    op.create_index("ix_print_identifiers_print_id", "print_identifiers", ["print_id"], unique=False)
    op.create_index("ix_print_identifiers_source", "print_identifiers", ["source"], unique=False)
    op.create_index("ix_print_identifiers_external_id", "print_identifiers", ["external_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_print_identifiers_external_id", table_name="print_identifiers")
    op.drop_index("ix_print_identifiers_source", table_name="print_identifiers")
    op.drop_index("ix_print_identifiers_print_id", table_name="print_identifiers")
    op.drop_table("print_identifiers")

    op.drop_index("ix_print_images_is_primary", table_name="print_images")
    op.drop_index("ix_print_images_print_id", table_name="print_images")
    op.drop_table("print_images")

    op.drop_index("ix_prints_is_foil", table_name="prints")
    op.drop_index("ix_prints_rarity", table_name="prints")
    op.drop_index("ix_prints_language", table_name="prints")
    op.drop_index("ix_prints_collector_number", table_name="prints")
    op.drop_index("ix_prints_set_id", table_name="prints")
    op.drop_index("ix_prints_card_id", table_name="prints")
    op.drop_table("prints")

    op.drop_index("ix_cards_name", table_name="cards")
    op.drop_index("ix_cards_game_id", table_name="cards")
    op.drop_table("cards")

    op.drop_index("ix_sets_name", table_name="sets")
    op.drop_index("ix_sets_code", table_name="sets")
    op.drop_index("ix_sets_game_id", table_name="sets")
    op.drop_table("sets")
