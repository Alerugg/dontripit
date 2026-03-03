"""add_scopes_and_metrics

Revision ID: 20260303_07
Revises: 20260303_06
Create Date: 2026-03-03 00:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "20260303_07"
down_revision: Union[str, None] = "20260303_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("scopes", sa.JSON(), nullable=True))
    op.execute("UPDATE api_keys SET scopes = '[\"read:catalog\"]'::json")
    op.alter_column("api_keys", "scopes", nullable=False)

    op.create_table(
        "api_request_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("endpoint", sa.String(length=255), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("period_ym", sa.String(length=7), nullable=False),
        sa.Column("api_key_prefix", sa.String(length=8), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_api_request_metrics_api_key_prefix"), "api_request_metrics", ["api_key_prefix"], unique=False)
    op.create_index(op.f("ix_api_request_metrics_endpoint"), "api_request_metrics", ["endpoint"], unique=False)
    op.create_index(op.f("ix_api_request_metrics_period_ym"), "api_request_metrics", ["period_ym"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_api_request_metrics_period_ym"), table_name="api_request_metrics")
    op.drop_index(op.f("ix_api_request_metrics_endpoint"), table_name="api_request_metrics")
    op.drop_index(op.f("ix_api_request_metrics_api_key_prefix"), table_name="api_request_metrics")
    op.drop_table("api_request_metrics")
    op.drop_column("api_keys", "scopes")
