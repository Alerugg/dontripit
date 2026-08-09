from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable

from sqlalchemy import select

from app.jobs.cardmarket_prices import (
    CARDMARKET_CURRENCY,
    CARDMARKET_SOURCE,
    CardmarketPriceRow,
)
from app.models import (
    Card,
    Game,
    PriceSnapshot,
    PriceSource,
    Print,
    PrintIdentifier,
    Product,
    ProductIdentifier,
    ProductVariant,
)

SUPPORTED_GAMES = {"pokemon", "onepiece", "mtg", "yugioh"}
PRODUCT_GROUPS = {"single", "non_single"}


@dataclass(frozen=True)
class MarketImportPlan:
    as_of: datetime
    game_slug: str
    product_group: str
    total_rows: int
    unique_products: int
    mapped_exact: int
    unmapped: int
    ambiguous: int
    cross_game_mappings: int
    wrong_entity_mappings: int
    duplicate_feed_rows: int
    missing_prices: int
    snapshots: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "game": self.game_slug,
            "product_group": self.product_group,
            "total_rows": self.total_rows,
            "unique_products": self.unique_products,
            "mapped_exact": self.mapped_exact,
            "unmapped": self.unmapped,
            "ambiguous": self.ambiguous,
            "cross_game_mappings": self.cross_game_mappings,
            "wrong_entity_mappings": self.wrong_entity_mappings,
            "duplicate_feed_rows": self.duplicate_feed_rows,
            "missing_prices": self.missing_prices,
            "snapshot_count": len(self.snapshots),
        }


def _utc(value: datetime | None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _money_payload(row: CardmarketPriceRow, *, entity_type: str, entity_id: int, game_slug: str, product_group: str, is_foil: bool = False) -> dict | None:
    if entity_type == "print" and is_foil:
        low = row.foil_low
        conservative = row.foil_low
        trend = row.foil_trend
        average = row.foil_avg
        avg1, avg7, avg30 = row.foil_avg1, row.foil_avg7, row.foil_avg30
        finish = "foil"
    else:
        low = row.low
        conservative = row.low_ex or row.low
        trend = row.trend
        average = row.avg
        avg1, avg7, avg30 = row.avg1, row.avg7, row.avg30
        finish = "nonfoil" if entity_type == "print" else None

    if all(value is None for value in (low, conservative, trend, average, avg1, avg7, avg30)):
        return None

    raw_json = {
        "idProduct": row.product_id,
        "feed_game": game_slug,
        "product_group": product_group,
        "avg1": str(avg1) if avg1 is not None else None,
        "avg7": str(avg7) if avg7 is not None else None,
        "avg30": str(avg30) if avg30 is not None else None,
    }
    if conservative is not None:
        raw_json["low_ex_plus"] = str(conservative)
    if finish:
        raw_json["finish"] = finish
    raw_json = {key: value for key, value in raw_json.items() if value is not None}

    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "currency": CARDMARKET_CURRENCY,
        "price_low": low,
        "price_mid": conservative,
        "price_high": None,
        "price_market": trend,
        "price_last": average,
        "quantity": None,
        "raw_json": raw_json,
    }


def _print_index(session) -> dict[str, list[tuple[int, bool, str]]]:
    rows = session.execute(
        select(PrintIdentifier.external_id, Print.id, Print.is_foil, Game.slug)
        .join(Print, Print.id == PrintIdentifier.print_id)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Card.game_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    result: dict[str, list[tuple[int, bool, str]]] = {}
    for external_id, entity_id, is_foil, game_slug in rows:
        result.setdefault(str(external_id), []).append((int(entity_id), bool(is_foil), str(game_slug)))
    return result


def _product_index(session) -> dict[str, list[tuple[int, str]]]:
    rows = session.execute(
        select(ProductIdentifier.external_id, ProductVariant.id, Game.slug)
        .join(ProductVariant, ProductVariant.id == ProductIdentifier.product_variant_id)
        .join(Product, Product.id == ProductVariant.product_id)
        .join(Game, Game.id == Product.game_id)
        .where(ProductIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    result: dict[str, list[tuple[int, str]]] = {}
    for external_id, entity_id, game_slug in rows:
        result.setdefault(str(external_id), []).append((int(entity_id), str(game_slug)))
    return result


def build_market_import_plan(
    session,
    rows: Iterable[CardmarketPriceRow],
    *,
    game_slug: str,
    product_group: str,
    as_of: datetime | None = None,
) -> MarketImportPlan:
    game_slug = str(game_slug or "").strip().lower()
    product_group = str(product_group or "").strip().lower()
    if game_slug not in SUPPORTED_GAMES:
        raise ValueError(f"Unsupported Don’tRipIt game slug: {game_slug!r}")
    if product_group not in PRODUCT_GROUPS:
        raise ValueError(f"Unsupported Cardmarket product group: {product_group!r}")

    rows = list(rows)
    by_product: dict[str, CardmarketPriceRow] = {}
    duplicate_feed_rows = 0
    for row in rows:
        if row.product_id in by_product:
            duplicate_feed_rows += 1
            continue
        by_product[row.product_id] = row

    print_index = _print_index(session)
    product_index = _product_index(session)
    snapshots: list[dict] = []
    mapped_exact = unmapped = ambiguous = cross_game = wrong_entity = missing_prices = 0

    for product_id, row in by_product.items():
        print_candidates = print_index.get(product_id, [])
        product_candidates = product_index.get(product_id, [])

        if product_group == "single":
            if product_candidates:
                wrong_entity += 1
                continue
            same_game = [candidate for candidate in print_candidates if candidate[2] == game_slug]
            if print_candidates and not same_game:
                cross_game += 1
                continue
            if not same_game:
                unmapped += 1
                continue
            if len(same_game) != 1:
                ambiguous += 1
                continue
            entity_id, is_foil, _ = same_game[0]
            payload = _money_payload(
                row,
                entity_type="print",
                entity_id=entity_id,
                game_slug=game_slug,
                product_group=product_group,
                is_foil=is_foil,
            )
        else:
            if print_candidates:
                wrong_entity += 1
                continue
            same_game = [candidate for candidate in product_candidates if candidate[1] == game_slug]
            if product_candidates and not same_game:
                cross_game += 1
                continue
            if not same_game:
                unmapped += 1
                continue
            if len(same_game) != 1:
                ambiguous += 1
                continue
            entity_id, _ = same_game[0]
            payload = _money_payload(
                row,
                entity_type="product_variant",
                entity_id=entity_id,
                game_slug=game_slug,
                product_group=product_group,
            )

        if payload is None:
            missing_prices += 1
            continue
        mapped_exact += 1
        snapshots.append(payload)

    return MarketImportPlan(
        as_of=_utc(as_of),
        game_slug=game_slug,
        product_group=product_group,
        total_rows=len(rows),
        unique_products=len(by_product),
        mapped_exact=mapped_exact,
        unmapped=unmapped,
        ambiguous=ambiguous,
        cross_game_mappings=cross_game,
        wrong_entity_mappings=wrong_entity,
        duplicate_feed_rows=duplicate_feed_rows,
        missing_prices=missing_prices,
        snapshots=tuple(snapshots),
    )


def validate_market_plan(plan: MarketImportPlan) -> list[str]:
    blockers = []
    if plan.ambiguous:
        blockers.append(f"ambiguous={plan.ambiguous}")
    if plan.cross_game_mappings:
        blockers.append(f"cross_game_mappings={plan.cross_game_mappings}")
    if plan.wrong_entity_mappings:
        blockers.append(f"wrong_entity_mappings={plan.wrong_entity_mappings}")
    if plan.duplicate_feed_rows:
        blockers.append(f"duplicate_feed_rows={plan.duplicate_feed_rows}")
    return blockers


def apply_market_import_plan(session, plan: MarketImportPlan) -> dict:
    blockers = validate_market_plan(plan)
    if blockers:
        raise ValueError("Refusing Cardmarket market import plan: " + ", ".join(blockers))

    source = session.execute(select(PriceSource).where(PriceSource.name == CARDMARKET_SOURCE)).scalar_one_or_none()
    if source is None:
        source = PriceSource(
            name=CARDMARKET_SOURCE,
            currency=CARDMARKET_CURRENCY,
            description="Cardmarket downloadable daily price guides; exact canonical mappings only.",
        )
        session.add(source)
        session.flush()
    elif source.currency != CARDMARKET_CURRENCY:
        raise ValueError(f"Cardmarket price source has unexpected currency {source.currency!r}")

    if not plan.snapshots:
        return {**plan.summary(), "inserted": 0, "updated": 0}

    entity_types = {payload["entity_type"] for payload in plan.snapshots}
    existing = session.execute(
        select(PriceSnapshot.entity_type, PriceSnapshot.entity_id).where(
            PriceSnapshot.entity_type.in_(entity_types),
            PriceSnapshot.source_id == source.id,
            PriceSnapshot.currency == CARDMARKET_CURRENCY,
            PriceSnapshot.as_of == plan.as_of,
        )
    ).all()
    existing_keys = {(str(entity_type), int(entity_id)) for entity_type, entity_id in existing}

    rows = [{"source_id": source.id, "as_of": plan.as_of, **payload} for payload in plan.snapshots]
    inserted = sum(1 for payload in plan.snapshots if (payload["entity_type"], payload["entity_id"]) not in existing_keys)
    updated = len(rows) - inserted

    dialect_name = session.get_bind().dialect.name
    if dialect_name == "postgresql":
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
        existing_rows = session.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.entity_type.in_(entity_types),
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == CARDMARKET_CURRENCY,
                PriceSnapshot.as_of == plan.as_of,
            )
        ).scalars().all()
        by_key = {(row.entity_type, row.entity_id): row for row in existing_rows}
        for row_data in rows:
            key = (row_data["entity_type"], row_data["entity_id"])
            current = by_key.get(key)
            if current is None:
                session.add(PriceSnapshot(**row_data))
                continue
            for field in ("price_low", "price_mid", "price_high", "price_market", "price_last", "quantity", "raw_json"):
                setattr(current, field, row_data[field])

    return {**plan.summary(), "inserted": inserted, "updated": updated}
