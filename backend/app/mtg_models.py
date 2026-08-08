from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import json_type


class MtgSourceSnapshot(Base):
    """Accepted Scryfall bulk snapshot provenance for the MTG catalog."""

    __tablename__ = "mtg_source_snapshots"
    __table_args__ = (
        UniqueConstraint("game_id", "source", "external_id", name="uq_mtg_snapshot_source_external"),
        Index("ix_mtg_source_snapshots_game", "game_id"),
        Index("ix_mtg_source_snapshots_updated", "source_updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counts_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    captured_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class MtgCatalogState(Base):
    """Pointer to the currently accepted MTG source snapshot."""

    __tablename__ = "mtg_catalog_state"

    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    active_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("mtg_source_snapshots.id", ondelete="SET NULL"), nullable=True
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class MtgSourcePrint(Base):
    """One source-level Scryfall printing object shared by exact finish Prints."""

    __tablename__ = "mtg_source_prints"
    __table_args__ = (
        UniqueConstraint("scryfall_id", name="uq_mtg_source_print_scryfall"),
        Index("ix_mtg_source_prints_snapshot", "snapshot_id"),
        Index("ix_mtg_source_prints_card", "card_id"),
        Index("ix_mtg_source_prints_set", "set_id"),
        Index("ix_mtg_source_prints_collector", "collector_number"),
        Index("ix_mtg_source_prints_language", "language"),
        Index("ix_mtg_source_prints_set_collector_language", "set_id", "collector_number", "language"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("mtg_source_snapshots.id", ondelete="RESTRICT"), nullable=False
    )
    scryfall_id: Mapped[str] = mapped_column(String(64), nullable=False)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    set_id: Mapped[int] = mapped_column(ForeignKey("sets.id", ondelete="CASCADE"), nullable=False)
    collector_number: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    rarity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    artist: Mapped[str | None] = mapped_column(String(255), nullable=True)
    illustration_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frame: Mapped[str | None] = mapped_column(String(32), nullable=True)
    border_color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    promo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    attributes_json: Mapped[dict] = mapped_column(json_type, nullable=False, default=dict)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
