"""add region to canonical set identity

Revision ID: 20260815_35
Revises: 20260814_34
Create Date: 2026-08-15 19:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_35"
down_revision: Union[str, None] = "20260814_34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


OLD_UNIQUE = "uq_sets_game_code"
NEW_UNIQUE = "uq_sets_game_code_region"
NEW_INDEX = "ix_sets_game_code_region"


def upgrade() -> None:
    # Keep the DB-level default deliberately: legacy ingesters that predate
    # regional set identity must continue creating the historical/global set,
    # never an accidental NULL/unknown region.
    op.add_column(
        "sets",
        sa.Column(
            "region",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'global'"),
        ),
    )
    op.drop_constraint(OLD_UNIQUE, "sets", type_="unique")
    op.create_unique_constraint(
        NEW_UNIQUE,
        "sets",
        ["game_id", "code", "region"],
    )
    op.create_index(
        NEW_INDEX,
        "sets",
        ["game_id", "code", "region"],
        unique=False,
    )


def downgrade() -> None:
    # The old schema cannot represent two physical regional sets that share
    # the same real product code. Refuse to destroy/collapse data silently.
    conn = op.get_bind()
    duplicate = conn.execute(
        sa.text(
            """
            SELECT game_id, lower(code) AS normalized_code, count(*) AS n
            FROM sets
            GROUP BY game_id, lower(code)
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).mappings().first()
    if duplicate:
        raise RuntimeError(
            "cannot downgrade set regional identity while duplicate regional "
            "set codes exist; remove/merge them explicitly first"
        )

    op.drop_index(NEW_INDEX, table_name="sets")
    op.drop_constraint(NEW_UNIQUE, "sets", type_="unique")
    op.create_unique_constraint(OLD_UNIQUE, "sets", ["game_id", "code"])
    op.drop_column("sets", "region")
