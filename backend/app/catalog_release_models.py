from __future__ import annotations

from datetime import date

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import json_type


class CatalogRelease(Base):
    """A source-defined commercial release, product, program or card-list series.

    This is deliberately separate from ``Set``. Set remains the canonical
    collector-number family used by the shared catalog, while CatalogRelease
    preserves how an exact print appeared in commercial products/programs.
    """

    __tablename__ = "catalog_releases"
    __table_args__ = (
        UniqueConstraint(
            "game_id",
            "source",
            "external_id",
            name="uq_catalog_release_source_external",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    code: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    release_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    region: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PrintRelease(Base):
    """Many-to-many provenance between an exact Print and a commercial release."""

    __tablename__ = "print_releases"
    __table_args__ = (
        UniqueConstraint(
            "print_id",
            "release_id",
            name="uq_print_release_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id", ondelete="CASCADE"), nullable=False, index=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("catalog_releases.id", ondelete="CASCADE"), nullable=False, index=True)
    source_print_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    appearance_type: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    metadata_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
