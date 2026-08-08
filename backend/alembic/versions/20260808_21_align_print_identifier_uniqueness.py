"""align print identifier uniqueness with canonical ORM contract

Revision ID: 20260808_21
Revises: 20260807_20
Create Date: 2026-08-08 13:35:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260808_21"
down_revision: Union[str, None] = "20260807_20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CONSTRAINT_NAME = "uq_identifier_source"


def upgrade() -> None:
    bind = op.get_bind()

    # Historical schema guaranteed one owner for each external identifier via
    # (source, external_id), but the canonical ORM also guarantees at most one
    # identifier per source for a physical Print. Validate legacy data before
    # making that ORM promise real in PostgreSQL.
    duplicate = bind.execute(sa.text(
        """
        SELECT print_id, source, COUNT(*) AS rows
        FROM print_identifiers
        GROUP BY print_id, source
        HAVING COUNT(*) > 1
        ORDER BY rows DESC, print_id, source
        LIMIT 1
        """
    )).mappings().first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot add uq_identifier_source: existing duplicate "
            f"print_id={duplicate['print_id']} source={duplicate['source']} rows={duplicate['rows']}"
        )

    inspector = sa.inspect(bind)
    existing = {row.get("name") for row in inspector.get_unique_constraints("print_identifiers")}
    if CONSTRAINT_NAME not in existing:
        op.create_unique_constraint(
            CONSTRAINT_NAME,
            "print_identifiers",
            ["print_id", "source"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {row.get("name") for row in inspector.get_unique_constraints("print_identifiers")}
    if CONSTRAINT_NAME in existing:
        op.drop_constraint(CONSTRAINT_NAME, "print_identifiers", type_="unique")
