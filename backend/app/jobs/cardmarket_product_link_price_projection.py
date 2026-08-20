from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select

from app.external_catalog_models import (
    ExternalCatalogProduct,
    ExternalCatalogProductVariantLink,
    ExternalMarketPriceSnapshot,
)
from app.models import Game, PriceSnapshot, PriceSource, Product, ProductVariant


CARDMARKET_SOURCE = "cardmarket"
CARDMARKET_CURRENCY = "EUR"
_ACCEPTED_STATUSES = {"accepted", "mapped", "exact"}


@dataclass(frozen=True)
class ProductLinkPriceProjectionPlan:
    game_slug: str
    catalog_capture: datetime | None
    as_of: datetime | None
    accepted_links: int
    canonical_variants: int
    priceable_variants: int
    missing_external_price: int
    ambiguous_variant_links: int
    ambiguous_external_links: int
    cross_game_links: int
    snapshots: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "source": CARDMARKET_SOURCE,
            "currency": CARDMARKET_CURRENCY,
            "game": self.game_slug,
            "catalog_capture": self.catalog_capture.isoformat() if self.catalog_capture else None,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "accepted_links": self.accepted_links,
            "canonical_variants": self.canonical_variants,
            "priceable_variants": self.priceable_variants,
            "missing_external_price": self.missing_external_price,
            "ambiguous_variant_links": self.ambiguous_variant_links,
            "ambiguous_external_links": self.ambiguous_external_links,
            "cross_game_links": self.cross_game_links,
            "snapshot_count": len(self.snapshots),
            "write_ready": (
                self.ambiguous_variant_links == 0
                and self.ambiguous_external_links == 0
                and self.cross_game_links == 0
            ),
        }


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _positive(value) -> bool:
    if value is None:
        return False
    try:
        return Decimal(str(value)) > 0
    except Exception:
        return False


def _has_meaningful_price(row: ExternalMarketPriceSnapshot) -> bool:
    # Canonical readers expose these fields. Moving averages remain provenance,
    # but an avg-only Cardmarket row is not a current public price.
    return any(
        _positive(value)
        for value in (row.price_low, row.price_mid, row.price_market, row.price_last)
    )


def _money_payload(
    external_price: ExternalMarketPriceSnapshot,
    *,
    variant_id: int,
    external_product: ExternalCatalogProduct,
    link: ExternalCatalogProductVariantLink,
) -> dict:
    return {
        "entity_type": "product_variant",
        "entity_id": int(variant_id),
        "currency": CARDMARKET_CURRENCY,
        "price_low": external_price.price_low if _positive(external_price.price_low) else None,
        "price_mid": external_price.price_mid if _positive(external_price.price_mid) else None,
        "price_high": None,
        "price_market": external_price.price_market if _positive(external_price.price_market) else None,
        "price_last": external_price.price_last if _positive(external_price.price_last) else None,
        "quantity": None,
        "raw_json": {
            "idProduct": str(external_product.external_id),
            "external_product_id": int(external_product.id),
            "price_variant": "sealed",
            "product_group": "non_single",
            "mapping_method": str(link.mapping_method),
            "mapping_confidence": str(link.confidence),
            "mapping_evidence": link.evidence or {},
            "website_path": external_product.website_path,
            "external_avg1": str(external_price.avg1) if external_price.avg1 is not None else None,
            "external_avg7": str(external_price.avg7) if external_price.avg7 is not None else None,
            "external_avg30": str(external_price.avg30) if external_price.avg30 is not None else None,
        },
    }


def build_product_link_price_projection_plan(
    session,
    *,
    game_slug: str,
    as_of: datetime | None = None,
) -> ProductLinkPriceProjectionPlan:
    """Project current Cardmarket non-single prices through accepted exact links.

    Identity is never inferred here. Only current-capture Cardmarket products with
    an already-accepted ExternalCatalogProductVariantLink can produce a canonical
    ProductVariant price. One-to-many or many-to-one claims are hard blockers.
    """
    game_slug = str(game_slug or "").strip().lower()
    if not game_slug:
        raise ValueError("game_slug is required")

    game = session.execute(select(Game).where(Game.slug == game_slug)).scalar_one_or_none()
    if game is None:
        raise ValueError(f"Unknown game slug {game_slug!r}")

    catalog_capture = session.execute(
        select(func.max(ExternalCatalogProduct.last_seen_at)).where(
            ExternalCatalogProduct.source == CARDMARKET_SOURCE,
            ExternalCatalogProduct.game_id == game.id,
            ExternalCatalogProduct.product_group == "non_single",
        )
    ).scalar_one_or_none()
    catalog_capture = _utc(catalog_capture)

    link_rows = []
    if catalog_capture is not None:
        link_rows = session.execute(
            select(
                ExternalCatalogProductVariantLink,
                ExternalCatalogProduct,
                ProductVariant.id,
                Product.game_id,
            )
            .join(
                ExternalCatalogProduct,
                ExternalCatalogProduct.id == ExternalCatalogProductVariantLink.external_product_id,
            )
            .join(ProductVariant, ProductVariant.id == ExternalCatalogProductVariantLink.product_variant_id)
            .join(Product, Product.id == ProductVariant.product_id)
            .where(
                ExternalCatalogProduct.source == CARDMARKET_SOURCE,
                ExternalCatalogProduct.game_id == game.id,
                ExternalCatalogProduct.product_group == "non_single",
                ExternalCatalogProduct.last_seen_at == catalog_capture,
                ExternalCatalogProductVariantLink.link_status.in_(_ACCEPTED_STATUSES),
            )
        ).all()

    by_variant: dict[int, list[tuple]] = {}
    by_external: dict[int, list[tuple]] = {}
    cross_game_links = 0
    for link, external_product, variant_id, product_game_id in link_rows:
        if int(product_game_id) != int(game.id):
            cross_game_links += 1
            continue
        row = (link, external_product, int(variant_id))
        by_variant.setdefault(int(variant_id), []).append(row)
        by_external.setdefault(int(external_product.id), []).append(row)

    ambiguous_variant_ids = {
        variant_id
        for variant_id, rows in by_variant.items()
        if len({int(row[1].id) for row in rows}) != 1
    }
    ambiguous_external_ids = {
        external_id
        for external_id, rows in by_external.items()
        if len({int(row[2]) for row in rows}) != 1
    }

    if as_of is None:
        as_of = session.execute(
            select(func.max(ExternalMarketPriceSnapshot.as_of))
            .join(
                ExternalCatalogProduct,
                ExternalCatalogProduct.id == ExternalMarketPriceSnapshot.external_product_id,
            )
            .where(
                ExternalCatalogProduct.source == CARDMARKET_SOURCE,
                ExternalCatalogProduct.game_id == game.id,
                ExternalCatalogProduct.product_group == "non_single",
                ExternalCatalogProduct.last_seen_at == catalog_capture,
            )
        ).scalar_one_or_none()
    as_of = _utc(as_of)

    external_ids = sorted(by_external)
    external_prices: dict[int, ExternalMarketPriceSnapshot] = {}
    if as_of is not None and external_ids:
        price_rows = session.execute(
            select(ExternalMarketPriceSnapshot).where(
                ExternalMarketPriceSnapshot.external_product_id.in_(external_ids),
                ExternalMarketPriceSnapshot.currency == CARDMARKET_CURRENCY,
                ExternalMarketPriceSnapshot.as_of == as_of,
                ExternalMarketPriceSnapshot.price_variant == "sealed",
            )
        ).scalars().all()
        external_prices = {int(row.external_product_id): row for row in price_rows}

    snapshots: list[dict] = []
    priceable_variants = 0
    missing_external_price = 0

    for variant_id, rows in sorted(by_variant.items()):
        if variant_id in ambiguous_variant_ids:
            continue
        link, external_product, _ = rows[0]
        if int(external_product.id) in ambiguous_external_ids:
            continue
        external_price = external_prices.get(int(external_product.id))
        if external_price is None or not _has_meaningful_price(external_price):
            missing_external_price += 1
            continue
        priceable_variants += 1
        snapshots.append(
            _money_payload(
                external_price,
                variant_id=variant_id,
                external_product=external_product,
                link=link,
            )
        )

    return ProductLinkPriceProjectionPlan(
        game_slug=game_slug,
        catalog_capture=catalog_capture,
        as_of=as_of,
        accepted_links=len(link_rows),
        canonical_variants=len(by_variant),
        priceable_variants=priceable_variants,
        missing_external_price=missing_external_price,
        ambiguous_variant_links=len(ambiguous_variant_ids),
        ambiguous_external_links=len(ambiguous_external_ids),
        cross_game_links=cross_game_links,
        snapshots=tuple(snapshots),
    )


def apply_product_link_price_projection_plan(session, plan: ProductLinkPriceProjectionPlan) -> dict:
    if plan.ambiguous_variant_links:
        raise ValueError(
            f"Refusing Cardmarket sealed projection with {plan.ambiguous_variant_links} ambiguous canonical ProductVariants"
        )
    if plan.ambiguous_external_links:
        raise ValueError(
            f"Refusing Cardmarket sealed projection with {plan.ambiguous_external_links} external products claiming multiple ProductVariants"
        )
    if plan.cross_game_links:
        raise ValueError(
            f"Refusing Cardmarket sealed projection with {plan.cross_game_links} cross-game links"
        )

    game = session.execute(select(Game).where(Game.slug == plan.game_slug)).scalar_one_or_none()
    if game is None:
        raise ValueError(f"Unknown game slug {plan.game_slug!r}")

    source = session.execute(
        select(PriceSource).where(PriceSource.name == CARDMARKET_SOURCE)
    ).scalar_one_or_none()
    if source is None:
        source = PriceSource(
            name=CARDMARKET_SOURCE,
            currency=CARDMARKET_CURRENCY,
            description=(
                "Cardmarket public daily Price Guide projected through accepted exact "
                "non-single ProductVariant links."
            ),
        )
        session.add(source)
        session.flush()
    elif source.currency != CARDMARKET_CURRENCY:
        raise ValueError(f"Cardmarket price source has unexpected currency {source.currency!r}")

    if plan.as_of is None or not plan.snapshots:
        return {**plan.summary(), "inserted": 0, "updated": 0}

    entity_ids = [int(payload["entity_id"]) for payload in plan.snapshots]
    existing = session.execute(
        select(PriceSnapshot.entity_id).where(
            PriceSnapshot.entity_type == "product_variant",
            PriceSnapshot.entity_id.in_(entity_ids),
            PriceSnapshot.source_id == source.id,
            PriceSnapshot.currency == CARDMARKET_CURRENCY,
            PriceSnapshot.as_of == plan.as_of,
        )
    ).scalars().all()
    existing_ids = {int(value) for value in existing}

    rows = [{"source_id": source.id, "as_of": plan.as_of, **payload} for payload in plan.snapshots]
    inserted = sum(1 for payload in plan.snapshots if int(payload["entity_id"]) not in existing_ids)
    updated = len(rows) - inserted

    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        update_fields = (
            "price_low",
            "price_mid",
            "price_high",
            "price_market",
            "price_last",
            "quantity",
            "raw_json",
        )
        for offset in range(0, len(rows), 500):
            statement = pg_insert(PriceSnapshot).values(rows[offset:offset + 500])
            statement = statement.on_conflict_do_update(
                constraint="uq_price_snapshot_identity",
                set_={field: getattr(statement.excluded, field) for field in update_fields},
            )
            session.execute(statement)
    else:
        current_rows = session.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.entity_type == "product_variant",
                PriceSnapshot.entity_id.in_(entity_ids),
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == CARDMARKET_CURRENCY,
                PriceSnapshot.as_of == plan.as_of,
            )
        ).scalars().all()
        by_id = {int(row.entity_id): row for row in current_rows}
        for row_data in rows:
            current = by_id.get(int(row_data["entity_id"]))
            if current is None:
                session.add(PriceSnapshot(**row_data))
                continue
            for field in update_fields if False else (
                "price_low",
                "price_mid",
                "price_high",
                "price_market",
                "price_last",
                "quantity",
                "raw_json",
            ):
                setattr(current, field, row_data[field])

    return {**plan.summary(), "inserted": inserted, "updated": updated}
