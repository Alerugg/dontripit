from __future__ import annotations

from statistics import median

from flask import Blueprint, jsonify, request
from sqlalchemy import func, select

from app import db
from app.models import Game, PriceDailyOHLC, PriceSnapshot, PriceSource, Print, ProductVariant, Set

prices_bp = Blueprint("prices", __name__)


def _json_error(error: str, detail: str, status: int):
    return jsonify({"error": error, "detail": detail}), status


def _resolve_entity(entity_type: str, entity_id: int):
    with db.SessionLocal() as session:
        if entity_type == "print":
            row = session.execute(select(Print.id, Print.collector_number, Print.language, Print.is_foil, Print.set_id).where(Print.id == entity_id)).mappings().first()
            if not row:
                return None
            set_row = session.execute(select(Set.code).where(Set.id == row["set_id"])).mappings().first()
            return {"id": row["id"], "type": "print", "collector_number": row["collector_number"], "language": row["language"], "is_foil": row["is_foil"], "set_code": set_row["code"] if set_row else None}
        if entity_type == "product_variant":
            row = session.execute(select(ProductVariant.id, ProductVariant.product_id, ProductVariant.language, ProductVariant.region).where(ProductVariant.id == entity_id)).mappings().first()
            if not row:
                return None
            return dict(row) | {"type": "product_variant"}
    return None


@prices_bp.get("/api/v1/prices")
def get_prices():
    entity_type = (request.args.get("entity_type") or "").strip()
    entity_id = request.args.get("entity_id", type=int)
    if entity_type not in {"print", "product_variant"}:
        return _json_error("invalid_params", "entity_type must be print|product_variant", 400)
    if entity_id is None:
        return _json_error("invalid_params", "entity_id is required", 400)

    source_name = (request.args.get("source") or "").strip()
    currency = (request.args.get("currency") or "").strip().upper()
    date_from = (request.args.get("from") or "").strip()
    date_to = (request.args.get("to") or "").strip()
    granularity = (request.args.get("granularity") or "").strip().lower()

    entity = _resolve_entity(entity_type, entity_id)
    if not entity:
        return _json_error("not_found", f"{entity_type} {entity_id} not found", 404)

    with db.SessionLocal() as session:
        base_daily = (
            select(
                PriceDailyOHLC.day,
                PriceDailyOHLC.open,
                PriceDailyOHLC.high,
                PriceDailyOHLC.low,
                PriceDailyOHLC.close,
                PriceDailyOHLC.volume,
                PriceSource.name.label("source"),
                PriceDailyOHLC.currency,
            )
            .join(PriceSource, PriceSource.id == PriceDailyOHLC.source_id)
            .where(PriceDailyOHLC.entity_type == entity_type, PriceDailyOHLC.entity_id == entity_id)
        )
        if source_name:
            base_daily = base_daily.where(PriceSource.name == source_name)
        if currency:
            base_daily = base_daily.where(PriceDailyOHLC.currency == currency)
        if date_from:
            base_daily = base_daily.where(PriceDailyOHLC.day >= date_from)
        if date_to:
            base_daily = base_daily.where(PriceDailyOHLC.day <= date_to)

        daily_rows = session.execute(base_daily.order_by(PriceDailyOHLC.day.asc())).mappings().all()

        use_daily = bool(daily_rows) and granularity != "raw"
        if use_daily:
            series = [
                {
                    "as_of": row["day"].isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": row["volume"],
                    "currency": row["currency"],
                    "source": row["source"],
                }
                for row in daily_rows
            ]
            return jsonify({"entity": entity, "granularity": "daily", "series": series})

        base_raw = (
            select(
                PriceSnapshot.as_of,
                PriceSnapshot.price_market,
                PriceSnapshot.price_low,
                PriceSnapshot.price_high,
                PriceSnapshot.price_last,
                PriceSnapshot.quantity,
                PriceSnapshot.currency,
                PriceSource.name.label("source"),
            )
            .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
            .where(PriceSnapshot.entity_type == entity_type, PriceSnapshot.entity_id == entity_id)
        )
        if source_name:
            base_raw = base_raw.where(PriceSource.name == source_name)
        if currency:
            base_raw = base_raw.where(PriceSnapshot.currency == currency)
        if date_from:
            base_raw = base_raw.where(func.date(PriceSnapshot.as_of) >= date_from)
        if date_to:
            base_raw = base_raw.where(func.date(PriceSnapshot.as_of) <= date_to)

        rows = session.execute(base_raw.order_by(PriceSnapshot.as_of.asc())).mappings().all()

    series = [
        {
            "as_of": row["as_of"].isoformat(),
            "market": float(row["price_market"]) if row["price_market"] is not None else None,
            "low": float(row["price_low"]) if row["price_low"] is not None else None,
            "high": float(row["price_high"]) if row["price_high"] is not None else None,
            "last": float(row["price_last"]) if row["price_last"] is not None else None,
            "qty": row["quantity"],
            "currency": row["currency"],
            "source": row["source"],
        }
        for row in rows
    ]
    return jsonify({"entity": entity, "granularity": "raw", "series": series})


@prices_bp.get("/api/v1/index")
def price_index():
    game = (request.args.get("game") or "").strip()
    set_code = (request.args.get("set_code") or "").strip()
    source_name = (request.args.get("source") or "").strip()
    currency = (request.args.get("currency") or "EUR").strip().upper()
    metric = (request.args.get("metric") or "median").strip().lower()
    if metric not in {"median", "mean"}:
        return _json_error("invalid_params", "metric must be median|mean", 400)

    with db.SessionLocal() as session:
        stmt = (
            select(PriceSnapshot.price_market, PriceSnapshot.as_of)
            .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
            .join(Print, Print.id == PriceSnapshot.entity_id)
            .join(Set, Set.id == Print.set_id)
            .join(Game, Game.id == Set.game_id)
            .where(PriceSnapshot.entity_type == "print", PriceSnapshot.price_market.is_not(None), PriceSnapshot.currency == currency)
        )
        if source_name:
            stmt = stmt.where(PriceSource.name == source_name)
        if game:
            stmt = stmt.where(Game.slug == game)
        if set_code:
            stmt = stmt.where(Set.code == set_code)

        rows = session.execute(stmt).all()

    if not rows:
        return jsonify({"value": None, "sample_size": 0, "as_of": None})

    values = [float(row[0]) for row in rows if row[0] is not None]
    as_of = max(row[1] for row in rows if row[1] is not None)
    if metric == "median":
        value = median(values)
    else:
        value = sum(values) / len(values)

    return jsonify({"value": round(value, 2), "sample_size": len(values), "as_of": as_of.isoformat(), "metric": metric, "currency": currency})


@prices_bp.get("/api/v1/admin/prices/last")
def admin_last_price_runs():
    source_name = (request.args.get("source") or "").strip()
    with db.SessionLocal() as session:
        stmt = (
            select(PriceSnapshot.as_of, PriceSource.name, PriceSnapshot.currency, func.count(PriceSnapshot.id))
            .join(PriceSource, PriceSource.id == PriceSnapshot.source_id)
            .group_by(PriceSnapshot.as_of, PriceSource.name, PriceSnapshot.currency)
            .order_by(PriceSnapshot.as_of.desc())
        )
        if source_name:
            stmt = stmt.where(PriceSource.name == source_name)
        rows = session.execute(stmt.limit(20)).all()

    return jsonify(
        [
            {
                "as_of": as_of.isoformat() if as_of else None,
                "source": source,
                "currency": currency,
                "snapshots": count,
            }
            for as_of, source, currency, count in rows
        ]
    )
