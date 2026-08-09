from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable

from sqlalchemy import select

from app.models import Card, Game, PriceSnapshot, PriceSource, Print, PrintIdentifier


CARDMARKET_SOURCE = "cardmarket"
CARDMARKET_CURRENCY = "EUR"


@dataclass(frozen=True)
class CardmarketPriceRow:
    product_id: str
    avg: Decimal | None = None
    low: Decimal | None = None
    low_ex: Decimal | None = None
    trend: Decimal | None = None
    avg1: Decimal | None = None
    avg7: Decimal | None = None
    avg30: Decimal | None = None
    foil_avg: Decimal | None = None
    foil_low: Decimal | None = None
    foil_trend: Decimal | None = None
    foil_avg1: Decimal | None = None
    foil_avg7: Decimal | None = None
    foil_avg30: Decimal | None = None


@dataclass(frozen=True)
class ImportPlan:
    as_of: datetime
    total_rows: int
    unique_products: int
    mapped_exact: int
    unmapped: int
    ambiguous: int
    cross_game_mappings: int
    duplicate_feed_rows: int
    missing_finish_prices: int
    game_slug: str | None
    snapshots: tuple[dict, ...]

    def summary(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "game": self.game_slug,
            "total_rows": self.total_rows,
            "unique_products": self.unique_products,
            "mapped_exact": self.mapped_exact,
            "unmapped": self.unmapped,
            "ambiguous": self.ambiguous,
            "cross_game_mappings": self.cross_game_mappings,
            "duplicate_feed_rows": self.duplicate_feed_rows,
            "missing_finish_prices": self.missing_finish_prices,
            "snapshot_count": len(self.snapshots),
        }


def _money(value) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not result.is_finite() or result < 0:
        return None
    return result.quantize(Decimal("0.01"))


def _first(mapping: dict, *names: str):
    normalized = {str(key).strip().lower(): value for key, value in mapping.items()}
    for name in names:
        key = name.strip().lower()
        if key in normalized:
            return normalized[key]
    return None


def _parse_row(raw: dict) -> CardmarketPriceRow | None:
    product_id = _first(raw, "idProduct", "id_product", "product_id")
    if product_id in (None, ""):
        return None
    product_id = str(product_id).strip()
    if not product_id:
        return None

    return CardmarketPriceRow(
        product_id=product_id,
        avg=_money(_first(raw, "avg", "Avg. Sell Price", "Avg Sell Price", "SELL")),
        low=_money(_first(raw, "low", "Low Price", "LOW")),
        low_ex=_money(_first(raw, "lowex", "lowex+", "Low Price Ex+", "LOWEX+", "LOWEX")),
        trend=_money(_first(raw, "trend", "Trend Price", "TREND")),
        avg1=_money(_first(raw, "avg1", "AVG1")),
        avg7=_money(_first(raw, "avg7", "AVG7")),
        avg30=_money(_first(raw, "avg30", "AVG30")),
        foil_avg=_money(_first(raw, "avg-holo", "foil avg", "Foil Sell", "SELLFOIL", "foil_sell")),
        foil_low=_money(_first(raw, "low-holo", "foil low", "Foil Low", "LOWFOIL", "foil_low")),
        foil_trend=_money(_first(raw, "trend-holo", "foil trend", "Foil Trend", "TRENDFOIL", "foil_trend")),
        foil_avg1=_money(_first(raw, "avg1-holo", "Foil AVG1", "AVG1FOIL", "foil_avg1")),
        foil_avg7=_money(_first(raw, "avg7-holo", "Foil AVG7", "AVG7FOIL", "foil_avg7")),
        foil_avg30=_money(_first(raw, "avg30-holo", "Foil AVG30", "AVG30FOIL", "foil_avg30")),
    )


def _parse_created_at(value) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_price_guide_bytes(content: bytes, *, filename: str = "") -> tuple[datetime | None, list[CardmarketPriceRow]]:
    """Parse current Cardmarket JSON exports and legacy CSV exports."""
    stripped = content.lstrip()
    rows: list[CardmarketPriceRow] = []
    created_at: datetime | None = None

    if filename.lower().endswith(".json") or stripped.startswith((b"{", b"[")):
        payload = json.loads(content.decode("utf-8-sig"))
        if isinstance(payload, dict):
            created_at = _parse_created_at(payload.get("createdAt") or payload.get("created_at"))
            raw_rows = payload.get("priceGuides") or payload.get("priceguides") or payload.get("data") or []
        elif isinstance(payload, list):
            raw_rows = payload
        else:
            raise ValueError("Unsupported Cardmarket JSON root")
        if not isinstance(raw_rows, list):
            raise ValueError("Cardmarket JSON priceGuides must be a list")
        for raw in raw_rows:
            if isinstance(raw, dict):
                parsed = _parse_row(raw)
                if parsed:
                    rows.append(parsed)
        return created_at, rows

    text = content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise ValueError("Cardmarket CSV has no header")
    for raw in reader:
        parsed = _parse_row(raw)
        if parsed:
            rows.append(parsed)
    return None, rows


def load_price_guide_file(path: str | Path) -> tuple[datetime | None, list[CardmarketPriceRow]]:
    source = Path(path)
    return load_price_guide_bytes(source.read_bytes(), filename=source.name)


def _snapshot_payload(row: CardmarketPriceRow, *, print_id: int, is_foil: bool, feed_game: str | None = None) -> dict | None:
    if is_foil:
        low = row.foil_low
        safe_low = row.foil_low
        trend = row.foil_trend
        average = row.foil_avg
        avg1, avg7, avg30 = row.foil_avg1, row.foil_avg7, row.foil_avg30
        finish = "foil"
    else:
        low = row.low
        safe_low = row.low_ex or row.low
        trend = row.trend
        average = row.avg
        avg1, avg7, avg30 = row.avg1, row.avg7, row.avg30
        finish = "nonfoil"

    if all(value is None for value in (low, safe_low, trend, average, avg1, avg7, avg30)):
        return None

    compact = {
        "idProduct": row.product_id,
        "finish": finish,
        "feed_game": feed_game,
        "low_ex_plus": str(safe_low) if safe_low is not None else None,
        "avg1": str(avg1) if avg1 is not None else None,
        "avg7": str(avg7) if avg7 is not None else None,
        "avg30": str(avg30) if avg30 is not None else None,
    }
    compact = {key: value for key, value in compact.items() if value is not None}
    return {
        "entity_type": "print",
        "entity_id": print_id,
        "currency": CARDMARKET_CURRENCY,
        "price_low": low,
        "price_mid": safe_low,
        "price_high": None,
        "price_market": trend,
        "price_last": average,
        "quantity": None,
        "raw_json": compact,
    }


def build_import_plan(
    session,
    rows: Iterable[CardmarketPriceRow],
    *,
    as_of: datetime | None = None,
    game_slug: str | None = None,
) -> ImportPlan:
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    as_of = as_of.astimezone(timezone.utc)
    game_slug = str(game_slug or "").strip().lower() or None

    rows = list(rows)
    by_product: dict[str, CardmarketPriceRow] = {}
    duplicates = 0
    for row in rows:
        if row.product_id in by_product:
            duplicates += 1
            continue
        by_product[row.product_id] = row

    identifiers = session.execute(
        select(PrintIdentifier.external_id, Print.id, Print.is_foil, Game.slug)
        .join(Print, Print.id == PrintIdentifier.print_id)
        .join(Card, Card.id == Print.card_id)
        .join(Game, Game.id == Card.game_id)
        .where(PrintIdentifier.source == CARDMARKET_SOURCE)
    ).all()
    mapped: dict[str, list[tuple[int, bool, str]]] = {}
    for external_id, print_id, is_foil, mapped_game in identifiers:
        mapped.setdefault(str(external_id), []).append((int(print_id), bool(is_foil), str(mapped_game)))

    snapshots: list[dict] = []
    exact = unmapped = ambiguous = cross_game = missing_finish = 0
    for product_id, row in by_product.items():
        all_candidates = mapped.get(product_id, [])
        if game_slug:
            candidates = [candidate for candidate in all_candidates if candidate[2] == game_slug]
            if all_candidates and not candidates:
                cross_game += 1
                continue
        else:
            candidates = all_candidates

        if not candidates:
            unmapped += 1
            continue
        if len(candidates) != 1:
            ambiguous += 1
            continue
        print_id, is_foil, mapped_game = candidates[0]
        payload = _snapshot_payload(row, print_id=print_id, is_foil=is_foil, feed_game=game_slug or mapped_game)
        if payload is None:
            missing_finish += 1
            continue
        exact += 1
        snapshots.append(payload)

    return ImportPlan(
        as_of=as_of,
        total_rows=len(rows),
        unique_products=len(by_product),
        mapped_exact=exact,
        unmapped=unmapped,
        ambiguous=ambiguous,
        cross_game_mappings=cross_game,
        duplicate_feed_rows=duplicates,
        missing_finish_prices=missing_finish,
        game_slug=game_slug,
        snapshots=tuple(snapshots),
    )


def _snapshot_row(source_id: int, plan: ImportPlan, payload: dict) -> dict:
    return {
        "source_id": source_id,
        "as_of": plan.as_of,
        **payload,
    }


def apply_import_plan(session, plan: ImportPlan) -> dict:
    """Idempotently upsert a preflighted exact-only plan with bounded DB round-trips."""
    if plan.cross_game_mappings:
        raise ValueError("Refusing Cardmarket import plan with cross-game mappings")

    source = session.execute(select(PriceSource).where(PriceSource.name == CARDMARKET_SOURCE)).scalar_one_or_none()
    if source is None:
        source = PriceSource(
            name=CARDMARKET_SOURCE,
            currency=CARDMARKET_CURRENCY,
            description="Cardmarket daily downloadable price guide; exact Print mappings only.",
        )
        session.add(source)
        session.flush()
    elif source.currency != CARDMARKET_CURRENCY:
        raise ValueError(f"Cardmarket price source has unexpected currency {source.currency!r}")

    if not plan.snapshots:
        return {**plan.summary(), "inserted": 0, "updated": 0}

    existing_ids = set(session.execute(
        select(PriceSnapshot.entity_id).where(
            PriceSnapshot.entity_type == "print",
            PriceSnapshot.source_id == source.id,
            PriceSnapshot.currency == CARDMARKET_CURRENCY,
            PriceSnapshot.as_of == plan.as_of,
        )
    ).scalars().all())

    inserted = sum(1 for payload in plan.snapshots if payload["entity_id"] not in existing_ids)
    updated = len(plan.snapshots) - inserted
    rows = [_snapshot_row(source.id, plan, payload) for payload in plan.snapshots]

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
        chunk_size = 500
        for offset in range(0, len(rows), chunk_size):
            chunk = rows[offset:offset + chunk_size]
            statement = pg_insert(PriceSnapshot).values(chunk)
            statement = statement.on_conflict_do_update(
                constraint="uq_price_snapshot_identity",
                set_={field: getattr(statement.excluded, field) for field in update_fields},
            )
            session.execute(statement)
    else:
        existing_rows = session.execute(
            select(PriceSnapshot).where(
                PriceSnapshot.entity_type == "print",
                PriceSnapshot.source_id == source.id,
                PriceSnapshot.currency == CARDMARKET_CURRENCY,
                PriceSnapshot.as_of == plan.as_of,
            )
        ).scalars().all()
        existing_by_id = {row.entity_id: row for row in existing_rows}
        new_rows = []
        for row_data in rows:
            existing = existing_by_id.get(row_data["entity_id"])
            if existing is None:
                new_rows.append(PriceSnapshot(**row_data))
                continue
            for field in (
                "price_low",
                "price_mid",
                "price_high",
                "price_market",
                "price_last",
                "quantity",
                "raw_json",
            ):
                setattr(existing, field, row_data[field])
        if new_rows:
            session.add_all(new_rows)

    return {**plan.summary(), "inserted": inserted, "updated": updated}
