from datetime import date

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

json_type = JSON().with_variant(JSONB, "postgresql")


class Game(Base):
    __tablename__ = "games"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Set(Base):
    __tablename__ = "sets"
    __table_args__ = (
        UniqueConstraint("game_id", "code", name="uq_sets_game_code"),
        UniqueConstraint("game_id", "tcgdex_id", name="uq_sets_game_tcgdex"),
        UniqueConstraint("game_id", "yugioh_id", name="uq_sets_game_yugioh"),
        UniqueConstraint("game_id", "riftbound_id", name="uq_sets_game_riftbound"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(String(50), nullable=False)
    tcgdex_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    yugioh_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    riftbound_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Card(Base):
    __tablename__ = "cards"
    __table_args__ = (
        UniqueConstraint("game_id", "name", name="uq_cards_game_name"),
        UniqueConstraint("game_id", "oracle_id", name="uq_cards_game_oracle"),
        UniqueConstraint("game_id", "tcgdex_id", name="uq_cards_game_tcgdex"),
        UniqueConstraint("game_id", "yugoprodeck_id", name="uq_cards_game_yugoprodeck"),
        UniqueConstraint("game_id", "riftbound_id", name="uq_cards_game_riftbound"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    oracle_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tcgdex_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    yugoprodeck_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    riftbound_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Print(Base):
    __tablename__ = "prints"
    __table_args__ = (
        UniqueConstraint("set_id", "card_id", "collector_number", name="uq_print_identity"),
        UniqueConstraint("scryfall_id", name="uq_prints_scryfall_id"),
        UniqueConstraint("tcgdex_id", name="uq_prints_tcgdex_id"),
        UniqueConstraint("yugioh_id", name="uq_prints_yugioh_id"),
        UniqueConstraint("riftbound_id", name="uq_prints_riftbound_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    set_id: Mapped[int] = mapped_column(ForeignKey("sets.id"), nullable=False, index=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id"), nullable=False, index=True)
    collector_number: Mapped[str] = mapped_column(String(50), nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rarity: Mapped[str | None] = mapped_column(String(100), nullable=True)
    is_foil: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    scryfall_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    tcgdex_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    yugioh_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    riftbound_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PrintImage(Base):
    __tablename__ = "print_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PrintIdentifier(Base):
    __tablename__ = "print_identifiers"
    __table_args__ = (UniqueConstraint("print_id", "source", name="uq_identifier_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    print_id: Mapped[int] = mapped_column(ForeignKey("prints.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    set_id: Mapped[int | None] = mapped_column(ForeignKey("sets.id"), nullable=True, index=True)
    product_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductVariant(Base):
    __tablename__ = "product_variants"
    __table_args__ = (UniqueConstraint("product_id", "language", "region", "packaging", name="uq_product_variant_identity"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False, index=True)
    language: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    region: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    packaging: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductImage(Base):
    __tablename__ = "product_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    source: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ProductIdentifier(Base):
    __tablename__ = "product_identifiers"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_product_identifier_source_external"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    product_variant_id: Mapped[int] = mapped_column(ForeignKey("product_variants.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("source_id", "checksum", name="uq_source_checksum"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    raw_json: Mapped[dict] = mapped_column(json_type, nullable=False)
    ingested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class PriceSource(Base):
    __tablename__ = "price_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (
        Index("ix_prices_source_game_captured", "source_id", "game_id", "captured_at"),
        Index("ix_prices_print_source_captured", "print_id", "source_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    print_id: Mapped[int | None] = mapped_column(ForeignKey("prints.id"), nullable=True, index=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id"), nullable=True, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("price_sources.id"), nullable=False, index=True)
    price: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    captured_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "source_id",
            "currency",
            "as_of",
            name="uq_price_snapshot_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("price_sources.id"), nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, index=True)
    price_low: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_mid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_high: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_market: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    price_last: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    as_of: Mapped[DateTime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    raw_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)


class PriceDailyOHLC(Base):
    __tablename__ = "price_daily_ohlc"
    __table_args__ = (
        UniqueConstraint(
            "entity_type",
            "entity_id",
            "source_id",
            "currency",
            "day",
            name="uq_price_daily_ohlc_identity",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[int] = mapped_column(ForeignKey("price_sources.id"), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    open: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)


class ApiPlan(Base):
    __tablename__ = "api_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    monthly_quota_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    burst_rpm: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(8), index=True, nullable=False)
    plan_id: Mapped[int] = mapped_column(ForeignKey("api_plans.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true", default=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    scopes: Mapped[list[str]] = mapped_column(json_type, nullable=False, default=lambda: ["read:catalog"])
    created_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_used_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("api_key_id", "period_ym", name="uq_api_usage_key_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    api_key_id: Mapped[int] = mapped_column(ForeignKey("api_keys.id"), nullable=False, index=True)
    period_ym: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_request_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class ApiRequestMetric(Base):
    __tablename__ = "api_request_metrics"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    period_ym: Mapped[str] = mapped_column(String(7), nullable=False, index=True)
    api_key_prefix: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    requested_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SourceSyncState(Base):
    __tablename__ = "source_sync_state"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, unique=True, index=True)
    cursor_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    last_run_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IngestRun(Base):
    __tablename__ = "ingest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), nullable=False, index=True)
    started_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    finished_at: Mapped[DateTime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    counts_json: Mapped[dict] = mapped_column(json_type, nullable=False, default=dict)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)


class FieldProvenance(Base):
    __tablename__ = "field_provenance"
    __table_args__ = (UniqueConstraint("entity_type", "entity_id", "field_name", "source", name="uq_field_provenance"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    entity_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_json: Mapped[dict | None] = mapped_column(json_type, nullable=True)
    updated_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SearchDocument(Base):
    __tablename__ = "search_documents"
    __table_args__ = (UniqueConstraint("doc_type", "object_id", name="uq_search_documents_doc_object"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    object_id: Mapped[int] = mapped_column(Integer, nullable=False)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    tsv: Mapped[str | None] = mapped_column(Text, nullable=True)
