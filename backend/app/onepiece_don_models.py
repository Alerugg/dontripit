from __future__ import annotations

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import json_type


class OnePieceDonPrint(Base):
    """Explicit classification/provenance for a physical One Piece DON!! print."""

    __tablename__ = "onepiece_don_prints"
    __table_args__ = (
        UniqueConstraint("print_id", name="uq_onepiece_don_prints_print"),
        UniqueConstraint("official_pdf_sha256", "official_image_object", name="uq_onepiece_don_prints_official_image"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_normalized: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    distribution_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    finish: Mapped[str | None] = mapped_column(String(100), nullable=True)
    official_listed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    official_pdf_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    official_pdf_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    official_pdf_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    official_image_object: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subject_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    distribution_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OnePieceDonOfficialItem(Base):
    """One row per artwork slot in Bandai's official DON!! Card List PDF."""

    __tablename__ = "onepiece_don_official_items"
    __table_args__ = (
        UniqueConstraint("pdf_sha256", "image_object", name="uq_onepiece_don_official_items_image"),
        UniqueConstraint("pdf_sha256", "page_number", "slot_number", name="uq_onepiece_don_official_items_slot"),
        UniqueConstraint("pdf_sha256", "sequence_number", name="uq_onepiece_don_official_items_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    pdf_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_number: Mapped[int] = mapped_column(Integer, nullable=False)
    image_object: Mapped[str] = mapped_column(String(32), nullable=False)
    image_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    image_phash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    distribution_label: Mapped[str | None] = mapped_column(Text, nullable=True)
    print_id: Mapped[int | None] = mapped_column(ForeignKey("prints.id", ondelete="SET NULL"), nullable=True, index=True)
    mapping_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mapping_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OnePieceDonEvidenceItem(Base):
    """Project evidence that must not be promoted to a canonical Print by guesswork."""

    __tablename__ = "onepiece_don_evidence_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    evidence_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True, index=True)
    evidence_kind: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_label: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    organization: Mapped[str | None] = mapped_column(String(255), nullable=True)
    physical_received: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    claimed_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    identity_status: Mapped[str] = mapped_column(String(32), nullable=False, default="unresolved", server_default="unresolved")
    evidence_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class OnePieceDonMarketItem(Base):
    """Source-owned DON market identity, deliberately not a canonical Print."""

    __tablename__ = "onepiece_don_market_items"
    __table_args__ = (
        UniqueConstraint("source", "metacard_external_id", name="uq_onepiece_don_market_source_meta"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    metacard_external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    representative_external_product_id: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    subject_normalized: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    product_ids_json: Mapped[list] = mapped_column(json_type, nullable=False)
    product_count: Mapped[int] = mapped_column(Integer, nullable=False)
    source_as_of: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False)
    official_item_id: Mapped[int | None] = mapped_column(ForeignKey("onepiece_don_official_items.id", ondelete="SET NULL"), nullable=True, index=True)
    mapping_source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    mapping_confidence: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
