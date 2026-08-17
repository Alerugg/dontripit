from __future__ import annotations

from collections import Counter, defaultdict
import logging
import re
import unicodedata

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


def _norm(value: str | None) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.casefold().replace("’", "'")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


def _release_core(name: str | None) -> str:
    raw = re.sub(r"\s*\[[^\]]+\]\s*$", "", str(name or "").strip()).strip()
    match = re.match(
        r"^(?:BOOSTER PACK|STARTER DECK|EXTRA BOOSTER|PREMIUM BOOSTER|ULTIMATE DECK)\s*-(.+)-\s*$",
        raw,
        flags=re.I,
    )
    return _norm(match.group(1) if match else raw)


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


def _target_onepiece_products(session, game_slug: str, set_code: str) -> dict:
    """Resolve only current Cardmarket products demonstrably tied to a One Piece set.

    Reprint/promo expansions can contain a handful of cards whose collector starts
    with the requested set code. Treating every such expansion as the requested
    release massively over-includes unrelated sealed products. Instead we accept:

    1. Cardmarket expansions with broad collector coverage of the requested set.
    2. Current non-single products that directly name the set code or official
       release title (Dash/Release packs may live in a separate Cardmarket expansion).
    """
    latest = session.execute(
        text("SELECT max(last_seen_at) FROM external_catalog_products WHERE source='cardmarket'")
    ).scalar_one_or_none()
    if latest is None:
        return {"product_ids": [], "expansion_ids": [], "primary_expansion_ids": []}

    set_name = session.execute(
        text(
            """
            SELECT s.name
            FROM sets s
            JOIN games g ON g.id = s.game_id
            WHERE g.slug = :game AND lower(s.code) = :set_code
            LIMIT 1
            """
        ),
        {"game": game_slug, "set_code": set_code},
    ).scalar_one_or_none()
    release_core = _release_core(set_name)

    collector_prefix = set_code.replace("-", "").upper()
    collector_rows = session.execute(
        text(
            """
            SELECT e.expansion_external_id, e.name
            FROM external_catalog_products e
            JOIN games g ON g.id = e.game_id
            WHERE e.source = 'cardmarket'
              AND e.product_group = 'single'
              AND e.last_seen_at = :latest
              AND g.slug = :game
              AND COALESCE(e.expansion_external_id, '') <> ''
              AND upper(e.name) LIKE :collector_pattern
            """
        ),
        {
            "game": game_slug,
            "latest": latest,
            "collector_pattern": f"%({collector_prefix}-%",
        },
    ).all()

    coverage: dict[str, set[str]] = defaultdict(set)
    collector_re = re.compile(rf"\(({re.escape(collector_prefix)}-\d{{3}})\)", re.I)
    for expansion_external_id, product_name in collector_rows:
        match = collector_re.search(str(product_name or ""))
        if match and expansion_external_id is not None:
            coverage[str(expansion_external_id)].add(match.group(1).upper())

    max_coverage = max((len(values) for values in coverage.values()), default=0)
    coverage_floor = max(20, int(max_coverage * 0.60)) if max_coverage else 20
    primary_expansions = {
        expansion_id
        for expansion_id, collectors in coverage.items()
        if len(collectors) >= coverage_floor
    }

    non_single_rows = session.execute(
        text(
            """
            SELECT e.id, e.external_id, e.expansion_external_id, e.name
            FROM external_catalog_products e
            JOIN games g ON g.id = e.game_id
            WHERE e.source = 'cardmarket'
              AND e.product_group = 'non_single'
              AND e.last_seen_at = :latest
              AND g.slug = :game
            """
        ),
        {"game": game_slug, "latest": latest},
    ).all()

    set_token = re.sub(r"[^a-z0-9]+", "", set_code.casefold())
    product_ids: set[int] = set()
    expansion_ids: set[str] = set()
    for external_product_id, _external_id, expansion_external_id, product_name in non_single_rows:
        expansion_id = str(expansion_external_id or "")
        compact_name = re.sub(r"[^a-z0-9]+", "", str(product_name or "").casefold())
        normalized_name = _norm(product_name)
        direct_name_match = bool(set_token and set_token in compact_name)
        direct_title_match = bool(release_core and len(release_core) >= 8 and release_core in normalized_name)
        if expansion_id in primary_expansions or direct_name_match or direct_title_match:
            product_ids.add(int(external_product_id))
            if expansion_id:
                expansion_ids.add(expansion_id)

    return {
        "product_ids": sorted(product_ids),
        "expansion_ids": sorted(expansion_ids),
        "primary_expansion_ids": sorted(primary_expansions),
        "collector_coverage": {key: len(value) for key, value in sorted(coverage.items())},
        "coverage_floor": coverage_floor,
    }


def _target_products(session, game_slug: str, set_code: str) -> dict:
    if game_slug == "onepiece":
        return _target_onepiece_products(session, game_slug, set_code)
    return {"product_ids": [], "expansion_ids": [], "primary_expansion_ids": []}


@market_search_read_bp.get("/api/v1/market/set-products/<game_slug>/<set_code>")
def cardmarket_set_products_read(game_slug: str, set_code: str):
    """Return current Cardmarket commercial products proven to belong to a set."""
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
            target = _target_products(session, game_slug, set_code)
            product_ids = target.get("product_ids") or []
            if not product_ids:
                return jsonify({
                    "items": [],
                    "limit": limit,
                    "offset": offset,
                    "total": 0,
                    "expansion_ids": [],
                    "primary_expansion_ids": [],
                    "categories": [],
                    "regions": [],
                })

            rows_sql = text(
                """
                WITH candidate_links AS (
                    SELECT e.id AS external_product_id,
                           min(e.external_id) AS external_id,
                           min(e.name) AS product_name,
                           min(e.category) AS category,
                           min(e.expansion_external_id) AS expansion_external_id,
                           min(e.website_path) AS website_path,
                           min(p.id) AS canonical_product_id,
                           min(p.product_type) AS product_type,
                           min(pv.id) AS canonical_product_variant_id,
                           min(pv.language) AS language,
                           min(pv.region) AS region
                    FROM external_catalog_products e
                    JOIN external_catalog_product_variant_links l ON l.external_product_id = e.id
                    JOIN product_variants pv ON pv.id = l.product_variant_id
                    JOIN products p ON p.id = pv.product_id
                    JOIN games g ON g.id = p.game_id
                    WHERE e.id IN :product_ids
                      AND e.source = 'cardmarket'
                      AND e.product_group = 'non_single'
                      AND l.link_status IN ('accepted', 'mapped', 'exact')
                      AND g.slug = :game
                    GROUP BY e.id
                    HAVING count(DISTINCT pv.id) = 1
                ),
                latest_capture AS (
                    SELECT max(mp.as_of) AS as_of
                    FROM external_market_price_snapshots mp
                    JOIN external_catalog_products ep ON ep.id = mp.external_product_id
                    JOIN games gg ON gg.id = ep.game_id
                    WHERE ep.source = 'cardmarket'
                      AND ep.product_group = 'non_single'
                      AND gg.slug = :game
                ),
                current_price AS (
                    SELECT DISTINCT ON (mp.external_product_id)
                           mp.external_product_id,
                           mp.currency,
                           mp.price_variant,
                           mp.price_low,
                           mp.price_mid,
                           mp.price_market,
                           mp.price_last,
                           mp.avg1,
                           mp.avg7,
                           mp.avg30,
                           mp.as_of AS price_as_of
                    FROM external_market_price_snapshots mp
                    JOIN latest_capture lc ON lc.as_of = mp.as_of
                    WHERE mp.currency = 'EUR'
                      AND mp.price_variant = 'sealed'
                      AND mp.external_product_id IN :product_ids
                    ORDER BY mp.external_product_id, mp.id DESC
                )
                SELECT cl.*,
                       cp.currency,
                       cp.price_variant,
                       cp.price_low,
                       cp.price_mid,
                       cp.price_market,
                       cp.price_last,
                       cp.avg1,
                       cp.avg7,
                       cp.avg30,
                       cp.price_as_of
                FROM candidate_links cl
                LEFT JOIN current_price cp ON cp.external_product_id = cl.external_product_id
                ORDER BY
                    CASE WHEN lower(COALESCE(cl.region, 'global')) = 'global' THEN 0 ELSE 1 END,
                    cl.category ASC,
                    cl.product_name ASC,
                    cl.external_product_id ASC
                """
            ).bindparams(bindparam("product_ids", expanding=True))
            rows = [
                dict(row)
                for row in session.execute(
                    rows_sql,
                    {"product_ids": product_ids, "game": game_slug},
                ).mappings().all()
            ]
    except SQLAlchemyError as error:
        logger.exception(
            "Cardmarket set product query failed for game=%s set=%s",
            game_slug,
            set_code,
            exc_info=error,
        )
        return jsonify({"error": "cardmarket_set_products_unavailable"}), 503

    def row_region(row: dict) -> str:
        return str(row.get("region") or "global").strip().lower() or "global"

    def row_category(row: dict) -> str:
        return str(row.get("category") or "Other").strip() or "Other"

    category_source = [row for row in rows if not region or row_region(row) == region]
    region_source = [row for row in rows if not category or row_category(row).casefold() == category.casefold()]
    category_counts = Counter(row_category(row) for row in category_source)
    region_counts = Counter(row_region(row) for row in region_source)

    filtered_rows = [
        row
        for row in rows
        if (not category or row_category(row).casefold() == category.casefold())
        and (not region or row_region(row) == region)
    ]
    total = len(filtered_rows)
    page_rows = filtered_rows[offset : offset + limit]

    items = []
    for row in page_rows:
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
        "expansion_ids": target.get("expansion_ids") or [],
        "primary_expansion_ids": target.get("primary_expansion_ids") or [],
        "categories": [
            {"value": value, "count": count}
            for value, count in sorted(category_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
        "regions": [
            {"value": value, "count": count}
            for value, count in sorted(region_counts.items(), key=lambda pair: (-pair[1], pair[0]))
        ],
    })
