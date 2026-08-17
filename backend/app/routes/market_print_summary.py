from __future__ import annotations

from decimal import Decimal
from urllib.parse import urljoin

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db


market_print_summary_bp = Blueprint("market_print_summary", __name__)
_MAX_PRINT_IDS = 100
_CARDMARKET_BASE = "https://www.cardmarket.com"


def _parse_ids(raw: str) -> list[int]:
    values: list[int] = []
    seen: set[int] = set()
    for token in str(raw or "").split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ValueError("ids must contain only integer print ids") from exc
        if value <= 0:
            raise ValueError("ids must contain positive print ids")
        if value not in seen:
            values.append(value)
            seen.add(value)
        if len(values) > _MAX_PRINT_IDS:
            raise ValueError(f"a maximum of {_MAX_PRINT_IDS} print ids can be requested")
    return values


def _number(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive_number(value):
    number = _number(value)
    return number if number is not None and number > 0 else None


def _cardmarket_url(raw_json: dict | None) -> str | None:
    raw_json = raw_json if isinstance(raw_json, dict) else {}
    path = str(raw_json.get("website_path") or "").strip()
    if not path:
        return None
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(_CARDMARKET_BASE, path if path.startswith("/") else f"/{path}")


@market_print_summary_bp.get("/api/v1/market/prints/summary")
def market_print_summary():
    """Return only current exact Cardmarket projections for up to 100 Prints.

    Identity is never inferred here. A canonical PriceSnapshot is visible only
    when all of these remain true now:
      * the Print has exactly one accepted current Cardmarket idProduct;
      * that idProduct is from the current Cardmarket catalog capture;
      * the canonical snapshot was projected at the latest PriceGuide capture
        for that game; and
      * the snapshot carries the same idProduct.

    Historical canonical snapshots remain available to historical APIs, but a
    missing row in today's Cardmarket PriceGuide can no longer masquerade as a
    current price in search/filter UI.
    """

    try:
        print_ids = _parse_ids(request.args.get("ids", ""))
    except ValueError as exc:
        return jsonify({"error": "invalid_ids", "detail": str(exc)}), 400

    if not print_ids:
        return jsonify({"items": []})

    sql = text(
        """
        WITH accepted AS (
          SELECT l.print_id,
                 MIN(e.external_id) AS id_product,
                 MIN(e.game_id) AS game_id,
                 COUNT(DISTINCT e.id) AS product_count
          FROM external_catalog_print_links l
          JOIN external_catalog_products e ON e.id = l.external_product_id
          WHERE e.source = 'cardmarket'
            AND e.product_group = 'single'
            AND l.link_status IN ('accepted','mapped','exact')
            AND l.print_id IN :print_ids
            AND e.last_seen_at = (
              SELECT MAX(e2.last_seen_at)
              FROM external_catalog_products e2
              WHERE e2.source = 'cardmarket'
                AND e2.game_id = e.game_id
            )
          GROUP BY l.print_id
          HAVING COUNT(DISTINCT e.id) = 1
        ), latest_game_capture AS (
          SELECT e.game_id,
                 MAX(mp.as_of) AS as_of
          FROM external_market_price_snapshots mp
          JOIN external_catalog_products e ON e.id = mp.external_product_id
          WHERE e.source = 'cardmarket'
            AND e.product_group = 'single'
          GROUP BY e.game_id
        ), current_projection AS (
          SELECT ps.*,
                 a.id_product,
                 ROW_NUMBER() OVER (
                   PARTITION BY ps.entity_id
                   ORDER BY ps.id DESC
                 ) AS row_rank
          FROM price_snapshots ps
          JOIN price_sources src ON src.id = ps.source_id
          JOIN accepted a ON a.print_id = ps.entity_id
          JOIN latest_game_capture lgc
            ON lgc.game_id = a.game_id
           AND lgc.as_of = ps.as_of
          WHERE ps.entity_type = 'print'
            AND lower(src.name) = 'cardmarket'
            AND ps.entity_id IN :print_ids
            AND COALESCE(ps.raw_json ->> 'idProduct', '') = a.id_product
        )
        SELECT entity_id AS print_id,
               currency,
               price_low,
               price_mid,
               price_high,
               price_market,
               price_last,
               as_of,
               raw_json
        FROM current_projection
        WHERE row_rank = 1
        ORDER BY entity_id ASC
        """
    ).bindparams(bindparam("print_ids", expanding=True))

    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, {"print_ids": print_ids}).mappings().all()
    except SQLAlchemyError:
        return jsonify({"error": "market_print_summary_unavailable"}), 503

    items = []
    for row in rows:
        raw_json = row.get("raw_json") if isinstance(row.get("raw_json"), dict) else {}
        market = _positive_number(row.get("price_market"))
        low = _positive_number(row.get("price_low"))
        mid = _positive_number(row.get("price_mid"))
        last = _positive_number(row.get("price_last"))
        high = _positive_number(row.get("price_high"))
        display = market if market is not None else low if low is not None else last if last is not None else mid
        if display is None:
            continue
        as_of = row.get("as_of")
        items.append(
            {
                "print_id": int(row["print_id"]),
                "currency": row.get("currency"),
                "price_market": market,
                "price_low": low,
                "price_mid": mid,
                "price_high": high,
                "price_last": last,
                "display_price": display,
                "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
                "cardmarket_id": str(raw_json.get("idProduct") or "") or None,
                "cardmarket_url": _cardmarket_url(raw_json),
                "mapping_method": raw_json.get("mapping_method"),
                "mapping_confidence": raw_json.get("mapping_confidence"),
                "finish": raw_json.get("finish"),
            }
        )

    return jsonify({"items": items})
