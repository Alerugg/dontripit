"""include card in physical print identity

Revision ID: 20260815_36
Revises: 20260815_35
Create Date: 2026-08-15

A visible collector number can legitimately be reused by different cards inside
one browse-family across distinct physical releases (verified for Japanese
Yu-Gi-Oh MP/WJ products). A Print is a physical printing of a specific Card, so
card_id belongs in the physical uniqueness key.

This migration deliberately preserves PostgreSQL's existing NULL semantics for
language and preserves the historical constraint name to avoid breaking code
or operational tooling that may refer to it by name.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260815_36"
down_revision: Union[str, None] = "20260815_35"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "uq_prints_set_number_language_is_foil_variant"
LOOKUP_INDEX_NAME = "ix_prints_set_number_language_foil_variant"


def _constraint_columns(bind, name: str) -> tuple[str, ...] | None:
    inspector = sa.inspect(bind)
    for constraint in inspector.get_unique_constraints("prints"):
        if constraint.get("name") == name:
            return tuple(constraint.get("column_names") or ())
    return None


def upgrade() -> None:
    bind = op.get_bind()
    current = _constraint_columns(bind, CONSTRAINT_NAME)
    expected_old = ("set_id", "collector_number", "language", "is_foil", "variant")
    expected_new = ("card_id", "set_id", "collector_number", "language", "is_foil", "variant")

    if current == expected_new:
        return
    if current != expected_old:
        raise RuntimeError(
            f"Refusing print identity migration: {CONSTRAINT_NAME} has unexpected columns {current!r}"
        )

    op.drop_constraint(CONSTRAINT_NAME, "prints", type_="unique")
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "prints",
        ["card_id", "set_id", "collector_number", "language", "is_foil", "variant"],
    )

    indexes = {index.get("name") for index in sa.inspect(bind).get_indexes("prints")}
    if LOOKUP_INDEX_NAME not in indexes:
        # Keep the former leading-column access path available for public set /
        # collector lookups now that the unique index begins with card_id.
        op.create_index(
            LOOKUP_INDEX_NAME,
            "prints",
            ["set_id", "collector_number", "language", "is_foil", "variant"],
            unique=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    current = _constraint_columns(bind, CONSTRAINT_NAME)
    expected_old = ("set_id", "collector_number", "language", "is_foil", "variant")
    expected_new = ("card_id", "set_id", "collector_number", "language", "is_foil", "variant")

    if current == expected_old:
        return
    if current != expected_new:
        raise RuntimeError(
            f"Refusing print identity downgrade: {CONSTRAINT_NAME} has unexpected columns {current!r}"
        )

    # PostgreSQL UNIQUE treats NULL values as distinct by default. Language is
    # the only nullable column in the old key, so only non-NULL duplicate groups
    # would make restoration of the old constraint unsafe.
    collisions = bind.execute(
        sa.text(
            """
            SELECT set_id, collector_number, language, is_foil, variant,
                   COUNT(*) AS row_count,
                   COUNT(DISTINCT card_id) AS card_count
            FROM prints
            WHERE language IS NOT NULL
            GROUP BY set_id, collector_number, language, is_foil, variant
            HAVING COUNT(*) > 1
            ORDER BY row_count DESC, set_id, collector_number
            LIMIT 20
            """
        )
    ).mappings().all()
    if collisions:
        raise RuntimeError(
            "Refusing downgrade to the old cross-card print identity because "
            f"physical slots are now legitimately shared by multiple Cards: {list(collisions)!r}"
        )

    indexes = {index.get("name") for index in sa.inspect(bind).get_indexes("prints")}
    if LOOKUP_INDEX_NAME in indexes:
        op.drop_index(LOOKUP_INDEX_NAME, table_name="prints")

    op.drop_constraint(CONSTRAINT_NAME, "prints", type_="unique")
    op.create_unique_constraint(
        CONSTRAINT_NAME,
        "prints",
        ["set_id", "collector_number", "language", "is_foil", "variant"],
    )
