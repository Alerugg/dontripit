"""enforce one primary image per Print

Revision ID: 20260912_42
Revises: 20260823_41
Create Date: 2026-09-12

A Print may retain multiple source images, but at most one image may be marked
primary. Production was explicitly cleaned and audited before this migration was
introduced, so the partial unique index is a fail-closed invariant rather than a
data-repair migration.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260912_42"
down_revision: Union[str, None] = "20260823_41"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "uq_print_images_one_primary_per_print"


def upgrade() -> None:
    # Refuse to hide data debt inside a schema migration. The cleanup must have
    # happened before this migration, and any unexpected duplicate must block it.
    duplicate_count = op.get_bind().execute(
        sa.text(
            """
            SELECT count(*)
            FROM (
                SELECT print_id
                FROM print_images
                WHERE is_primary IS TRUE
                GROUP BY print_id
                HAVING count(*) > 1
            ) q
            """
        )
    ).scalar_one()
    if int(duplicate_count or 0) != 0:
        raise RuntimeError(
            f"refusing primary-image uniqueness migration: duplicate prints={duplicate_count}"
        )

    op.create_index(
        INDEX_NAME,
        "print_images",
        ["print_id"],
        unique=True,
        postgresql_where=sa.text("is_primary IS TRUE"),
    )


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="print_images")
