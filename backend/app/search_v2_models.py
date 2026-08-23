from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import json_type


class CardSearchProfile(Base):
    """Rebuildable search/filter projection for a logical Card."""

    __tablename__ = "card_search_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    aliases_json: Mapped[list | None] = mapped_column(json_type, nullable=True)
    keywords_json: Mapped[list | None] = mapped_column(json_type, nullable=True)
    attributes_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_don: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    don_subject_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class PrintSearchProfile(Base):
    """Rebuildable search/filter projection for an exact physical Print."""

    __tablename__ = "print_search_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_set_code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    normalized_collector_number: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    rarity: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    exact_variant: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    variant_family: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    release_names_json: Mapped[list | None] = mapped_column(json_type, nullable=True)
    aliases_json: Mapped[list | None] = mapped_column(json_type, nullable=True)
    keywords_json: Mapped[list | None] = mapped_column(json_type, nullable=True)
    attributes_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_don: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    don_subject_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class FacetDefinition(Base):
    """Game-specific advanced-filter definition consumed dynamically by the UI."""

    __tablename__ = "facet_definitions"
    __table_args__ = (
        UniqueConstraint("game_id", "scope", "key", name="uq_facet_definition_game_scope_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    value_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ui_type: Mapped[str] = mapped_column(String(32), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    source_path: Mapped[str] = mapped_column(String(255), nullable=False)
    multi_value: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    filterable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    sortable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    searchable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    quick_filter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    options_json: Mapped[dict | list | None] = mapped_column(json_type, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
