from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models import json_type


class ExternalCatalogProduct(Base):
    """A source-owned commercial product, never a Don’tRipIt canonical identity.

    Cardmarket's ``idProduct`` lives here. It may represent a market page whose
    offers span more than one canonical Print dimension (for example finish or
    language). Links to canonical entities are therefore separate and can be
    one-to-many without weakening ``Card != Print``.
    """

    __tablename__ = "external_catalog_products"
    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_external_catalog_source_id"),
        Index("ix_external_catalog_source_game_group", "source", "game_id", "product_group"),
        Index("ix_external_catalog_source_expansion", "source", "expansion_external_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    product_group: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    category_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    expansion_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    date_added: Mapped[date | None] = mapped_column(Date, nullable=True)
    metacard_external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    website_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    source_updated_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    first_seen_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)


class ExternalCatalogPrintLink(Base):
    __tablename__ = "external_catalog_print_links"
    __table_args__ = (
        UniqueConstraint("external_product_id", "print_id", name="uq_external_catalog_print_link"),
        Index("ix_external_catalog_print_link_status", "link_status", "confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_product_id: Mapped[int] = mapped_column(
        ForeignKey("external_catalog_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id", ondelete="CASCADE"), nullable=False, index=True)
    mapping_method: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", server_default="candidate")
    link_status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", server_default="candidate")
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    evidence: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ExternalCatalogProductVariantLink(Base):
    __tablename__ = "external_catalog_product_variant_links"
    __table_args__ = (
        UniqueConstraint("external_product_id", "product_variant_id", name="uq_external_catalog_variant_link"),
        Index("ix_external_catalog_variant_link_status", "link_status", "confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_product_id: Mapped[int] = mapped_column(
        ForeignKey("external_catalog_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_variant_id: Mapped[int] = mapped_column(
        ForeignKey("product_variants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    mapping_method: Mapped[str] = mapped_column(String(100), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", server_default="candidate")
    link_status: Mapped[str] = mapped_column(String(32), nullable=False, default="candidate", server_default="candidate")
    reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    evidence: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ExternalMarketPriceSnapshot(Base):
    """Source-level market price before projection onto canonical entities.

    ``price_variant`` is deliberately part of identity because one Cardmarket
    idProduct can contain both non-foil and foil price guides. Keeping this at
    the external-product layer prevents a source page from being mistaken for a
    single physical Don’tRipIt Print.
    """

    __tablename__ = "external_market_price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "external_product_id",
            "currency",
            "price_variant",
            "as_of",
            name="uq_external_market_price_identity",
        ),
        Index("ix_external_market_price_product_variant_asof", "external_product_id", "price_variant", "as_of"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    external_product_id: Mapped[int] = mapped_column(
        ForeignKey("external_catalog_products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    price_variant: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    price_low: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_mid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_market: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_last: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    avg1: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    avg7: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    avg30: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    as_of: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    raw_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
