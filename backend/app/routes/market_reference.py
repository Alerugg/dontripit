from __future__ import annotations

from urllib.parse import urlparse

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db


market_reference_bp = Blueprint("market_reference", __name__)
_ACCEPTED_LINK_STATUSES = ("accepted", "mapped", "exact")
_UNSUPPORTED_FINISH_TOKENS = ("etched", "glossy")


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _float(value):
    return float(value) if value is not None else None


def _cardmarket_url(website_path: str | None) -> str | None:
    raw = str(website_path or "").strip()
    if not raw:
        return None
    if raw.startswith("/"):
        return f"https://www.cardmarket.com{raw}"

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host in {"cardmarket.com", "www.cardmarket.com"}:
        return raw
    return None


def _price_payload(row: dict, *, finish: str | None = None) -> dict | None:
    if not row or row.get("price_as_of") is None:
        return None

    minimum = _float(row.get("price_low"))
    conservative = _float(row.get("price_mid"))
    trend = _float(row.get("price_market"))
    average = _float(row.get("price_last"))
    value = conservative if conservative is not None else trend if trend is not None else average if average is not None else minimum
    if value is None:
        return None

    as_of = row.get("price_as_of")
    return {
        "value": value,
        "minimum": minimum,
        "conservative": conservative,
        "trend": trend,
        "average": average,
        "avg1": _float(row.get("avg1")),
        "avg7": _float(row.get("avg7")),
        "avg30": _float(row.get("avg30")),
        "currency": row.get("currency") or "EUR",
        "source": "cardmarket",
        "as_of": as_of.isoformat() if hasattr(as_of, "isoformat") else as_of,
        "finish": finish,
        "price_variant": row.get("price_variant"),
    }


def _reference_payload(row: dict) -> dict:
    return {
        "provider": "cardmarket",
        "id_product": str(row.get("external_id") or ""),
        "external_product_id": row.get("external_product_id"),
        "product_name": row.get("product_name"),
        "website_path": row.get("website_path"),
        "url": _cardmarket_url(row.get("website_path")),
        "mapping_method": row.get("mapping_method"),
        "mapping_confidence": row.get("mapping_confidence"),
    }


def _load_print_market_rows(session, print_ids: list[int]) -> list[dict]:
    if not print_ids:
        return []

    sql = text(
        """
        WITH latest_prices AS (
            SELECT external_product_id,
                   currency,
                   price_variant,
                   price_low,
                   price_mid,
                   price_market,
                   price_last,
                   avg1,
                   avg7,
                   avg30,
                   as_of AS price_as_of,
                   ROW_NUMBER() OVER (
                       PARTITION BY external_product_id, currency, price_variant
                       ORDER BY as_of DESC, id DESC
                   ) AS row_number
            FROM external_market_price_snapshots
            WHERE currency = 'EUR'
              AND price_variant IN ('nonfoil', 'foil')
        )
        SELECT l.print_id,
               p.is_foil,
               p.variant,
               e.id AS external_product_id,
               e.external_id,
               e.name AS product_name,
               e.website_path,
               l.mapping_method,
               l.confidence AS mapping_confidence,
               lp.currency,
               lp.price_variant,
               lp.price_low,
               lp.price_mid,
               lp.price_market,
               lp.price_last,
               lp.avg1,
               lp.avg7,
               lp.avg30,
               lp.price_as_of
        FROM external_catalog_print_links l
        JOIN external_catalog_products e ON e.id = l.external_product_id
        JOIN prints p ON p.id = l.print_id
        LEFT JOIN latest_prices lp
          ON lp.external_product_id = e.id
         AND lp.row_number = 1
         AND lp.price_variant = CASE WHEN p.is_foil IS TRUE THEN 'foil' ELSE 'nonfoil' END
        WHERE l.print_id IN :print_ids
          AND e.source = 'cardmarket'
          AND e.product_group = 'single'
          AND l.link_status IN ('accepted', 'mapped', 'exact')
        ORDER BY l.print_id ASC, e.id ASC
        """
    ).bindparams(bindparam("print_ids", expanding=True))
    return [dict(row) for row in session.execute(sql, {"print_ids": print_ids}).mappings().all()]


def _build_print_market_payloads(rows: list[dict], requested_ids: list[int]) -> dict[int, dict]:
    by_print: dict[int, list[dict]] = {}
    for row in rows:
        by_print.setdefault(int(row["print_id"]), []).append(row)

    output: dict[int, dict] = {}
    for print_id in requested_ids:
        candidates = by_print.get(int(print_id), [])
        product_ids = {int(row["external_product_id"]) for row in candidates if row.get("external_product_id") is not None}
        if not candidates:
            output[int(print_id)] = {
                "print_id": int(print_id),
                "status": "unmapped",
                "reference": None,
                "price": None,
                "reason": "no_accepted_exact_cardmarket_mapping",
            }
            continue
        if len(product_ids) != 1:
            output[int(print_id)] = {
                "print_id": int(print_id),
                "status": "ambiguous",
                "reference": None,
                "price": None,
                "reason": "multiple_accepted_cardmarket_products",
            }
            continue

        row = candidates[0]
        variant = str(row.get("variant") or "").strip().lower()
        unsupported_finish = any(token in variant for token in _UNSUPPORTED_FINISH_TOKENS)
        finish = variant or ("foil" if row.get("is_foil") else "nonfoil")
        reference = _reference_payload(row)
        price = None if unsupported_finish else _price_payload(row, finish=finish)
        if unsupported_finish:
            status = "mapped_unpriced"
            reason = "cardmarket_priceguide_cannot_separate_finish"
        elif price is None:
            status = "mapped_unpriced"
            reason = "no_current_cardmarket_priceguide_row"
        else:
            status = "priced"
            reason = None

        output[int(print_id)] = {
            "print_id": int(print_id),
            "status": status,
            "reference": reference,
            "price": price,
            "reason": reason,
        }
    return output


@market_reference_bp.post("/api/v1/market/prints/cardmarket/batch")
def cardmarket_print_batch():
    body = request.get_json(silent=True) or {}
    raw_ids = body.get("print_ids") if isinstance(body, dict) else None
    if not isinstance(raw_ids, list):
        return jsonify({"error": "invalid_params", "detail": "print_ids must be a list"}), 400

    print_ids: list[int] = []
    seen = set()
    for raw_id in raw_ids:
        try:
            print_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if print_id <= 0 or print_id in seen:
            continue
        seen.add(print_id)
        print_ids.append(print_id)
    if not print_ids:
        return jsonify({"items": []})
    if len(print_ids) > 50:
        return jsonify({"error": "invalid_params", "detail": "maximum 50 print_ids"}), 400

    try:
        with db.SessionLocal() as session:
            rows = _load_print_market_rows(session, print_ids)
    except SQLAlchemyError:
        return jsonify({"error": "cardmarket_reference_unavailable"}), 503

    payloads = _build_print_market_payloads(rows, print_ids)
    return jsonify({"items": [payloads[print_id] for print_id in print_ids]})


@market_reference_bp.get("/api/v1/market/prints/<int:print_id>/cardmarket")
def cardmarket_print_reference(print_id: int):
    try:
        with db.SessionLocal() as session:
            rows = _load_print_market_rows(session, [print_id])
    except SQLAlchemyError:
        return jsonify({"error": "cardmarket_reference_unavailable"}), 503

    return jsonify(_build_print_market_payloads(rows, [print_id])[print_id])


@market_reference_bp.get("/api/v1/market/sets/<game_slug>/<set_code>/products")
def cardmarket_set_products(game_slug: str, set_code: str):
    game_slug = str(game_slug or "").strip().lower()
    set_code = str(set_code or "").strip().lower()
    if not game_slug or not set_code:
        return jsonify({"error": "invalid_params", "detail": "game and set_code are required"}), 400

    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=1000)
    category = str(request.args.get("category") or "").strip()
    category_clause = "AND lower(COALESCE(e.category, '')) = lower(:category)" if category else ""
    params = {"game": game_slug, "set_code": set_code, "limit": limit, "offset": offset, "category": category}

    base_join = """
        FROM external_catalog_products e
        JOIN external_catalog_product_variant_links l ON l.external_product_id = e.id
        JOIN product_variants pv ON pv.id = l.product_variant_id
        JOIN products p ON p.id = pv.product_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = p.game_id
        WHERE e.source = 'cardmarket'
          AND e.product_group = 'non_single'
          AND l.link_status IN ('accepted', 'mapped', 'exact')
          AND g.slug = :game
          AND lower(s.code) = :set_code
    """

    rows_sql = text(
        f"""
        WITH latest_prices AS (
            SELECT external_product_id,
                   currency,
                   price_variant,
                   price_low,
                   price_mid,
                   price_market,
                   price_last,
                   avg1,
                   avg7,
                   avg30,
                   as_of AS price_as_of,
                   ROW_NUMBER() OVER (
                       PARTITION BY external_product_id, currency, price_variant
                       ORDER BY as_of DESC, id DESC
                   ) AS row_number
            FROM external_market_price_snapshots
            WHERE currency = 'EUR'
              AND price_variant = 'sealed'
        ), exact_products AS (
            SELECT e.id AS external_product_id,
                   MIN(e.external_id) AS external_id,
                   MIN(e.name) AS product_name,
                   MIN(e.category) AS category,
                   MIN(e.website_path) AS website_path,
                   MIN(p.id) AS canonical_product_id,
                   MIN(p.product_type) AS product_type,
                   COUNT(DISTINCT pv.id) AS variant_count
            {base_join}
            {category_clause}
            GROUP BY e.id
            HAVING COUNT(DISTINCT pv.id) = 1
        )
        SELECT ep.*,
               lp.currency,
               lp.price_variant,
               lp.price_low,
               lp.price_mid,
               lp.price_market,
               lp.price_last,
               lp.avg1,
               lp.avg7,
               lp.avg30,
               lp.price_as_of
        FROM exact_products ep
        LEFT JOIN latest_prices lp
          ON lp.external_product_id = ep.external_product_id
         AND lp.row_number = 1
        ORDER BY ep.product_name ASC, ep.external_product_id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(
        f"""
        SELECT COUNT(*) FROM (
            SELECT e.id
            {base_join}
            {category_clause}
            GROUP BY e.id
            HAVING COUNT(DISTINCT pv.id) = 1
        ) exact_products
        """
    )
    categories_sql = text(
        f"""
        SELECT COALESCE(NULLIF(trim(e.category), ''), 'Other') AS category,
               COUNT(DISTINCT e.id) AS product_count
        {base_join}
        GROUP BY COALESCE(NULLIF(trim(e.category), ''), 'Other')
        ORDER BY product_count DESC, category ASC
        """
    )

    try:
        with db.SessionLocal() as session:
            rows = [dict(row) for row in session.execute(rows_sql, params).mappings().all()]
            total = int(session.execute(count_sql, params).scalar_one())
            category_rows = [dict(row) for row in session.execute(categories_sql, params).mappings().all()]
    except SQLAlchemyError:
        return jsonify({"error": "cardmarket_set_products_unavailable"}), 503

    items = []
    for row in rows:
        price = _price_payload(row, finish="sealed")
        items.append(
            {
                "type": "sealed",
                "id": row.get("external_product_id"),
                "external_product_id": row.get("external_product_id"),
                "external_id": str(row.get("external_id") or ""),
                "name": row.get("product_name"),
                "category": row.get("category"),
                "product_type": row.get("product_type"),
                "canonical_product_id": row.get("canonical_product_id"),
                "set_code": set_code,
                "game": game_slug,
                "cardmarket": {
                    "provider": "cardmarket",
                    "id_product": str(row.get("external_id") or ""),
                    "website_path": row.get("website_path"),
                    "url": _cardmarket_url(row.get("website_path")),
                },
                "price": price,
            }
        )

    return jsonify(
        {
            "items": items,
            "limit": limit,
            "offset": offset,
            "total": total,
            "categories": [
                {"value": row.get("category"), "count": int(row.get("product_count") or 0)}
                for row in category_rows
            ],
        }
    )
