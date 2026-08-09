from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select

from app.external_catalog_models import ExternalCatalogProduct, ExternalMarketPriceSnapshot
from app.jobs.cardmarket_prices import CARDMARKET_CURRENCY, CARDMARKET_SOURCE, CardmarketPriceRow
from app.models import Game


@dataclass(frozen=True)
class ExternalPricePlan:
    as_of: datetime
    game_slug: str | None
    total_rows: int
    unique_products: int
    duplicate_rows: int
    matched_external_products: int
    missing_external_products: int
    cross_game_products: int
    snapshot_count: int
    nonfoil_snapshots: int
    foil_snapshots: int
    sealed_snapshots: int
    snapshots: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "source": CARDMARKET_SOURCE,
            "currency": CARDMARKET_CURRENCY,
            "as_of": self.as_of.isoformat(),
            "game": self.game_slug,
            "total_rows": self.total_rows,
            "unique_products": self.unique_products,
            "duplicate_rows": self.duplicate_rows,
            "matched_external_products": self.matched_external_products,
            "missing_external_products": self.missing_external_products,
            "cross_game_products": self.cross_game_products,
            "snapshot_count": self.snapshot_count,
            "nonfoil_snapshots": self.nonfoil_snapshots,
            "foil_snapshots": self.foil_snapshots,
            "sealed_snapshots": self.sealed_snapshots,
            "write_ready": (
                self.duplicate_rows == 0
                and self.missing_external_products == 0
                and self.cross_game_products == 0
            ),
        }


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has(*values: Decimal | None) -> bool:
    return any(value is not None for value in values)


def _payload(
    *,
    external_product_id: int,
    product_id: str,
    price_variant: str,
    low: Decimal | None,
    conservative: Decimal | None,
    trend: Decimal | None,
    average: Decimal | None,
    avg1: Decimal | None,
    avg7: Decimal | None,
    avg30: Decimal | None,
    as_of: datetime,
) -> dict | None:
    if not _has(low, conservative, trend, average, avg1, avg7, avg30):
        return None
    return {
        "external_product_id": external_product_id,
        "currency": CARDMARKET_CURRENCY,
        "price_variant": price_variant,
        "price_low": low,
        "price_mid": conservative,
        "price_market": trend,
        "price_last": average,
        "avg1": avg1,
        "avg7": avg7,
        "avg30": avg30,
        "as_of": as_of,
        "raw_json": {
            "idProduct": product_id,
            "source": CARDMARKET_SOURCE,
            "price_variant": price_variant,
        },
    }


def build_external_price_plan(
    session,
    rows: Iterable[CardmarketPriceRow],
    *,
    as_of: datetime | None = None,
    game_slug: str | None = None,
) -> ExternalPricePlan:
    """Map a Cardmarket Price Guide to source-owned external products.

    No canonical Print mapping is required here. A Cardmarket single can emit a
    nonfoil and a foil snapshot at the same timestamp. Non-single products emit
    one ``sealed`` snapshot from the normal price block.
    """
    as_of = _utc(as_of)
    game_slug = str(game_slug or "").strip().lower() or None
    rows = list(rows)

    by_product: dict[str, CardmarketPriceRow] = {}
    duplicate_rows = 0
    for row in rows:
        if row.product_id in by_product:
            duplicate_rows += 1
            continue
        by_product[row.product_id] = row

    catalog_rows = session.execute(
        select(
            ExternalCatalogProduct.external_id,
            ExternalCatalogProduct.id,
            ExternalCatalogProduct.product_group,
            Game.slug,
        )
        .join(Game, Game.id == ExternalCatalogProduct.game_id)
        .where(ExternalCatalogProduct.source == CARDMARKET_SOURCE)
    ).all()
    catalog = {
        str(external_id): (int(external_product_id), str(product_group), str(mapped_game))
        for external_id, external_product_id, product_group, mapped_game in catalog_rows
    }

    snapshots: list[dict] = []
    matched = missing = cross_game = 0
    nonfoil_count = foil_count = sealed_count = 0

    for product_id, row in by_product.items():
        mapped = catalog.get(product_id)
        if mapped is None:
            missing += 1
            continue
        external_product_id, product_group, mapped_game = mapped
        if game_slug and mapped_game != game_slug:
            cross_game += 1
            continue
        matched += 1

        if product_group == "single":
            nonfoil = _payload(
                external_product_id=external_product_id,
                product_id=product_id,
                price_variant="nonfoil",
                low=row.low,
                conservative=row.low_ex or row.low,
                trend=row.trend,
                average=row.avg,
                avg1=row.avg1,
                avg7=row.avg7,
                avg30=row.avg30,
                as_of=as_of,
            )
            if nonfoil is not None:
                snapshots.append(nonfoil)
                nonfoil_count += 1

            foil = _payload(
                external_product_id=external_product_id,
                product_id=product_id,
                price_variant="foil",
                low=row.foil_low,
                conservative=row.foil_low,
                trend=row.foil_trend,
                average=row.foil_avg,
                avg1=row.foil_avg1,
                avg7=row.foil_avg7,
                avg30=row.foil_avg30,
                as_of=as_of,
            )
            if foil is not None:
                snapshots.append(foil)
                foil_count += 1
            continue

        if product_group == "non_single":
            sealed = _payload(
                external_product_id=external_product_id,
                product_id=product_id,
                price_variant="sealed",
                low=row.low,
                # Card condition EX+ is a single-card concept. For sealed
                # products conservative MVP value is the current low.
                conservative=row.low,
                trend=row.trend,
                average=row.avg,
                avg1=row.avg1,
                avg7=row.avg7,
                avg30=row.avg30,
                as_of=as_of,
            )
            if sealed is not None:
                snapshots.append(sealed)
                sealed_count += 1

    return ExternalPricePlan(
        as_of=as_of,
        game_slug=game_slug,
        total_rows=len(rows),
        unique_products=len(by_product),
        duplicate_rows=duplicate_rows,
        matched_external_products=matched,
        missing_external_products=missing,
        cross_game_products=cross_game,
        snapshot_count=len(snapshots),
        nonfoil_snapshots=nonfoil_count,
        foil_snapshots=foil_count,
        sealed_snapshots=sealed_count,
        snapshots=tuple(snapshots),
    )


def apply_external_price_plan(session, plan: ExternalPricePlan) -> dict:
    if plan.duplicate_rows:
        raise ValueError(f"Refusing Cardmarket price apply with {plan.duplicate_rows} duplicate feed rows")
    if plan.missing_external_products:
        raise ValueError(
            f"Refusing Cardmarket price apply with {plan.missing_external_products} products missing from external catalog"
        )
    if plan.cross_game_products:
        raise ValueError(f"Refusing Cardmarket price apply with {plan.cross_game_products} cross-game products")
    if not plan.snapshots:
        return {**plan.summary(), "written": 0}

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        update_fields = (
            "price_low",
            "price_mid",
            "price_market",
            "price_last",
            "avg1",
            "avg7",
            "avg30",
            "raw_json",
        )
        chunk_size = 1000
        for offset in range(0, len(plan.snapshots), chunk_size):
            chunk = list(plan.snapshots[offset:offset + chunk_size])
            statement = pg_insert(ExternalMarketPriceSnapshot).values(chunk)
            statement = statement.on_conflict_do_update(
                constraint="uq_external_market_price_identity",
                set_={field: getattr(statement.excluded, field) for field in update_fields},
            )
            session.execute(statement)
    else:
        keys = {
            (payload["external_product_id"], payload["currency"], payload["price_variant"], payload["as_of"])
            for payload in plan.snapshots
        }
        product_ids = sorted({key[0] for key in keys})
        existing_rows = session.execute(
            select(ExternalMarketPriceSnapshot).where(
                ExternalMarketPriceSnapshot.external_product_id.in_(product_ids),
                ExternalMarketPriceSnapshot.as_of == plan.as_of,
            )
        ).scalars().all()
        existing = {
            (row.external_product_id, row.currency, row.price_variant, _utc(row.as_of)): row
            for row in existing_rows
        }
        for payload in plan.snapshots:
            key = (
                payload["external_product_id"],
                payload["currency"],
                payload["price_variant"],
                payload["as_of"],
            )
            row = existing.get(key)
            if row is None:
                session.add(ExternalMarketPriceSnapshot(**payload))
                continue
            for field in (
                "price_low",
                "price_mid",
                "price_market",
                "price_last",
                "avg1",
                "avg7",
                "avg30",
                "raw_json",
            ):
                setattr(row, field, payload[field])

    return {**plan.summary(), "written": len(plan.snapshots)}
