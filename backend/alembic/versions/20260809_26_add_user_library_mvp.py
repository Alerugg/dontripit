"""add user auth, collection and wishlist tables

Revision ID: 20260809_26
Revises: 20260808_25
Create Date: 2026-08-09 04:20:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260809_26"
down_revision: Union[str, None] = "20260808_25"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("marketing_consent", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("marketing_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "user_collection_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("quantity", sa.Integer(), server_default="1", nullable=False),
        sa.Column("condition", sa.String(length=32), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("purchase_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("purchase_currency", sa.String(length=3), nullable=True),
        sa.Column("acquired_at", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "print_id", name="uq_user_collection_print"),
    )
    op.create_index("ix_user_collection_items_user_id", "user_collection_items", ["user_id"])
    op.create_index("ix_user_collection_items_print_id", "user_collection_items", ["print_id"])

    op.create_table(
        "user_wishlist_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("print_id", sa.Integer(), sa.ForeignKey("prints.id", ondelete="CASCADE"), nullable=False),
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("target_price", sa.Numeric(12, 2), nullable=True),
        sa.Column("target_currency", sa.String(length=3), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "print_id", name="uq_user_wishlist_print"),
    )
    op.create_index("ix_user_wishlist_items_user_id", "user_wishlist_items", ["user_id"])
    op.create_index("ix_user_wishlist_items_print_id", "user_wishlist_items", ["print_id"])


def downgrade() -> None:
    op.drop_table("user_wishlist_items")
    op.drop_table("user_collection_items")
    op.drop_table("user_sessions")
    op.drop_table("users")
