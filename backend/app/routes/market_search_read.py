from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.routes.market_reference import (
    _build_print_market_payloads,
    _cardmarket_url,
    _load_print_market_rows,
    _price_payload,
)


market_search_read_bp = Blueprint("market_search_read", __name__)
logger = logging.getLogger(__name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _parse_ids(raw: str) -> list[int]:
    result: list[int] = []
    seen = set()
    for token in str(raw or "").split(","):
        try:
            value = int(token.strip())
        except (TypeError, ValueError):
            continue
        if value <= 0 or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


@market_search_read_bp.get("/api/v1/market/prints/cardmarket-batch")
def cardmarket_print_batch_read():
    print_ids = _parse_ids(request.args.get("ids", ""))
    if not print_ids:
        return jsonify({"items": []})
    if len(print_ids) > 50:
        return jsonify({"error": "invalid_params", "detail": "maximum 50 print ids"}), 400

    try:
        with db.SessionLocal() as session:
            rows = _load_print_market_rows(session, print_ids)
    except SQLAlchemyError:
        return jsonify({"error": "cardmarket_reference_unavailable"}), 503

    payloads = _build_print_market_payloads(rows, print_ids)
    return jsonify({"items": [payloads[print_id] for print_id in print_ids]})


def _target_expansions(session, game_slug: str, set_code: str) -> list[str]:
    # One Piece Cardmarket single names end in exact collectors such as
    # "Krieg (OP15-001)". Matching the literal collector prefix is both safer
    # and dramatically cheaper than normalizing every catalog row at request time.
    if game_slug == "onepiece":
        collector_prefix = set_code.replace("-", "").lower()
        sql = text(
            """
            SELECT DISTINCT e.expansion_external_id
            FROM external_catalog_products e
            JOIN games g ON g.id = e.game_id
            WHERE e.source = 'cardmarket'
              AND e.product_group = 'single'
              AND g.slug = :game
              AND COALESCE(e.expansion_external_id, '') <> ''
              AND lower(e.name) LIKE :collector_pattern
            ORDER BY e.expansion_external_id
            """
        )
        pattern = f"%({collector_prefix}-%"
        return [
            str(value)
            for value in session.execute(sql, {"game": game_slug, "collector_pattern": pattern}).scalars().all()
            if value is not None
        ]

    return []


@market_search_read_bp.get("/api/v1/market/set-products/<game_slug>/<set_code>")
def cardmarket_set_products_read(game_slug: str, set_code: str):
    """Return Cardmarket commercial products from expansions proven by set collectors."""
    game_slug = str(game_slug or "").strip().lower()
    set_code = str(set_code or "").strip().lower()
    if not game_slug or not set_code:
        return jsonify({"error": "invalid_params"}), 400

    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=5000)
    category = str(request.args.get("category") or "").strip()
    region = str(request.args.get("region") or "").strip().lower()

    try:
        with db.SessionLocal() as session:
            expansion_ids = _target_expansions(session, game_slug, set_code)
            if not expansion_ids:
                return jsonify({
                    "items": [], "limit": limit, "offset": offset, "total": 0,
                    "expansion_ids": [], "categories": [], "regions": [],
                })

            params = {
                "game": game_slug,
                "expansion_ids": expansion_ids,
                "limit": limit,
                "offset": offset,
                "category": category,
                "region": region,
            }
            category_clause = "AND lower(COALESCE(e.category, '')) = lower(:category)" if category else ""
            region_clause = "AND lower(COALESCE(pv.region, 'global')) = :region" if region else ""

            base_join = f"""
                FROM external_catalog_products e
                JOIN external_catalog_product_variant_links l ON l.external_product_id = e.id
                JOIN product_variants pv ON pv.id = l.product_variant_id
                JOIN products p ON p.id = pv.product_id
                JOIN games g ON g.id = p.game_id
                WHERE e.source = 'cardmarket'
                  AND e.product_group = 'non_single'
                  AND e.expansion_external_id IN :expansion_ids
                  AND l.link_status IN ('accepted', 'mapped', 'exact')
                  AND g.slug = :game
                  {category_clause}
                  {region_clause}
            """

            rows_sql = text(
                f"""
                WITH candidate_products AS (
                  SELECT DISTINCT e.id
                  {base_join}
                ),
                latest_as_of AS (
                  SELECT mp.external_product_id, max(mp.as_of) AS as_of
                  FROM external_market_price_snapshots mp
                  JOIN candidate_products cp ON cp.id = mp.external_product_id
                  WHERE mp.currency = 'EUR'
                  GROUP BY mp.external_product_id
                ),
                current_price_candidates AS (
                  SELECT mp.*,
                         count(*) OVER (PARTITION BY mp.external_product_id) AS total_variants,
                         count(*) FILTER (WHERE mp.price_variant IN ('default', 'nonfoil'))
                           OVER (PARTITION BY mp.external_product_id) AS preferred_variants
                  FROM external_market_price_snapshots mp
                  JOIN latest_as_of la
                    ON la.external_product_id = mp.external_product_id AND la.as_of = mp.as_of
                  WHERE mp.currency = 'EUR'
                ),
                eligible_price AS (
                  SELECT * FROM current_price_candidates
                  WHERE (preferred_variants = 1 AND price_variant IN ('default', 'nonfoil'))
                     OR (preferred_variants = 0 AND total_variants = 1)
                )
                SELECT e.id AS external_product_id,
                       e.external_id,
                       e.name AS product_name,
                       e.category,
                       e.expansion_external_id,
                       e.website_path,
                       p.id AS canonical_product_id,
                       p.product_type,
                       pv.id AS canonical_product_variant_id,
                       pv.language,
                       pv.region,
                       ep.currency,
                       ep.price_variant,
                       ep.price_low,
                       ep.price_mid,
                       ep.price_market,
                       ep.price_last,
                       ep.avg1,
                       ep.avg7,
                       ep.avg30,
                       ep.as_of AS price_as_of
                {base_join}
                LEFT JOIN eligible_price ep ON ep.external_product_id = e.id
                ORDER BY
                  CASE WHEN lower(COALESCE(pv.region, 'global')) = 'global' THEN 0 ELSE 1 END,
                  e.category ASC,
                  e.name ASC,
                  e.id ASC
                LIMIT :limit OFFSET :offset
                """
            ).bindparams(bindparam("expansion_ids", expanding=True))

            count_sql = text(
                f"SELECT count(DISTINCT e.id) {base_join}"
            ).bindparams(bindparam("expansion_ids", expanding=True))

            category_base = base_join.replace(category_clause, "") if category_clause else base_join
            categories_sql = text(
                f"""
                SELECT COALESCE(NULLIF(trim(e.category), ''), 'Other') AS category,
                       count(DISTINCT e.id) AS product_count
                {category_base}
                GROUP BY COALESCE(NULLIF(trim(e.category), ''), 'Other')
                ORDER BY product_count DESC, category ASC
                """
            ).bindparams(bindparam("expansion_ids", expanding=True))

            region_base = base_join.replace(region_clause, "") if region_clause else base_join
            regions_sql = text(
                f"""
                SELECT COALESCE(NULLIF(lower(trim(pv.region)), ''), 'global') AS region,
                       count(DISTINCT e.id) AS product_count
                {region_base}
                GROUP BY COALESCE(NULLIF(lower(trim(pv.region)), ''), 'global')
                ORDER BY product_count DESC, region ASC
                """
            ).bindparams(bindparam("expansion_ids", expanding=True))

            rows = [dict(row) for row in session.execute(rows_sql, params).mappings().all()]
            total = int(session.execute(count_sql, params).scalar_one())
            category_rows = [dict(row) for row in session.execute(categories_sql, params).mappings().all()]
            region_rows = [dict(row) for row in session.execute(regions_sql, params).mappings().all()]
    except SQLAlchemyError as error:
        logger.exception(
            "Cardmarket set product query failed for game=%s set=%s",
            game_slug,
            set_code,
            exc_info=error,
        )
        return jsonify({"error": "cardmarket_set_products_unavailable"}), 503

    items = []
    for row in rows:
        items.append({
            "type": "sealed",
            "id": row.get("external_product_id"),
            "external_product_id": row.get("external_product_id"),
            "external_id": str(row.get("external_id") or ""),
            "name": row.get("product_name"),
            "category": row.get("category"),
            "product_type": row.get("product_type"),
            "canonical_product_id": row.get("canonical_product_id"),
            "canonical_product_variant_id": row.get("canonical_product_variant_id"),
            "language": row.get("language"),
            "region": row.get("region") or "global",
            "set_code": set_code,
            "game": game_slug,
            "expansion_external_id": row.get("expansion_external_id"),
            "cardmarket": {
                "provider": "cardmarket",
                "id_product": str(row.get("external_id") or ""),
                "website_path": row.get("website_path"),
                "url": _cardmarket_url(row.get("website_path")),
            },
            "price": _price_payload(row, finish="sealed"),
        })

    return jsonify({
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "expansion_ids": expansion_ids,
        "categories": [
            {"value": row.get("category"), "count": int(row.get("product_count") or 0)}
            for row in category_rows
        ],
        "regions": [
            {"value": row.get("region"), "count": int(row.get("product_count") or 0)}
            for row in region_rows
        ],
    })
