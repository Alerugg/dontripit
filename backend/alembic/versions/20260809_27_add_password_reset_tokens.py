"""add one-time password reset tokens

Revision ID: 20260809_27
Revises: 20260809_26
Create Date: 2026-08-09 12:12:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "20260809_27"
down_revision: Union[str, None] = "20260809_26"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_password_reset_tokens",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_user_password_reset_token_hash"),
    )
    op.create_index("ix_user_password_reset_tokens_user_id", "user_password_reset_tokens", ["user_id"])
    op.create_index("ix_user_password_reset_tokens_token_hash", "user_password_reset_tokens", ["token_hash"])
    op.create_index("ix_user_password_reset_tokens_expires_at", "user_password_reset_tokens", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_user_password_reset_tokens_expires_at", table_name="user_password_reset_tokens")
    op.drop_index("ix_user_password_reset_tokens_token_hash", table_name="user_password_reset_tokens")
    op.drop_index("ix_user_password_reset_tokens_user_id", table_name="user_password_reset_tokens")
    op.drop_table("user_password_reset_tokens")
