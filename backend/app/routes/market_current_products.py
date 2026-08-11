from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db


market_current_products_bp = Blueprint("market_current_products", __name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _positive(value):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


@market_current_products_bp.get("/api/v1/market/current-products")
def list_current_market_products():
    """Browse only the current Cardmarket capture with current-only prices.

    This is the public collector-facing reader. Historical Cardmarket products
    remain in the source-owned catalog for audit/history, but they cannot leak a
    stale price into the current UI. Identity and price availability are separate:
    an exact product may be current while its latest PriceGuide row is absent.
    """
    game = str(request.args.get("game") or "").strip().lower()
    group = str(request.args.get("group") or "non_single").strip().lower()
    category = str(request.args.get("category") or "").strip()
    q = str(request.args.get("q") or "").strip()
    if not game:
        return jsonify({"error": "invalid_params", "detail": "game is required"}), 400
    if group not in {"single", "non_single"}:
        return jsonify({"error": "invalid_params", "detail": "group must be single or non_single"}), 400

    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=100)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=100000)
    where_extra = []
    params = {"game": game, "group": group, "limit": limit, "offset": offset}
    if category:
        where_extra.append("lower(COALESCE(e.category,'')) = lower(:category)")
        params["category"] = category
    if q:
        where_extra.append("lower(e.name) LIKE :q")
        params["q"] = f"%{q.lower()}%"
    extra_sql = (" AND " + " AND ".join(where_extra)) if where_extra else ""

    sql = text(
        f"""
        WITH target_game AS (
          SELECT id FROM games WHERE slug = :game LIMIT 1
        ), latest_catalog AS (
          SELECT MAX(e.last_seen_at) AS seen
          FROM external_catalog_products e
          JOIN target_game tg ON tg.id = e.game_id
          WHERE e.source = 'cardmarket'
        ), current_products AS (
          SELECT e.*
          FROM external_catalog_products e
          JOIN target_game tg ON tg.id = e.game_id
          JOIN latest_catalog lc ON lc.seen = e.last_seen_at
          WHERE e.source = 'cardmarket'
            AND e.product_group = :group
            {extra_sql}
        ), accepted_links AS (
          SELECT l.external_product_id,
                 COUNT(DISTINCT l.product_variant_id) AS variant_count,
                 MIN(l.product_variant_id) AS product_variant_id,
                 COUNT(DISTINCT p.game_id) AS linked_game_count,
                 MIN(p.game_id) AS linked_game_id,
                 MIN(p.id) AS canonical_product_id
          FROM external_catalog_product_variant_links l
          JOIN product_variants pv ON pv.id = l.product_variant_id
          JOIN products p ON p.id = pv.product_id
          JOIN current_products e ON e.id = l.external_product_id
          WHERE l.link_status IN ('accepted','mapped','exact')
          GROUP BY l.external_product_id
        ), all_links AS (
          SELECT l.external_product_id, COUNT(*) AS link_count
          FROM external_catalog_product_variant_links l
          JOIN current_products e ON e.id = l.external_product_id
          GROUP BY l.external_product_id
        ), latest_price_capture AS (
          SELECT MAX(mp.as_of) AS as_of
          FROM external_market_price_snapshots mp
          JOIN current_products e ON e.id = mp.external_product_id
        ), current_prices AS (
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
                 mp.as_of
          FROM external_market_price_snapshots mp
          JOIN current_products e ON e.id = mp.external_product_id
          JOIN latest_price_capture lpc ON lpc.as_of = mp.as_of
          WHERE mp.currency = 'EUR'
            AND (
              (:group = 'non_single' AND mp.price_variant = 'sealed')
              OR (:group = 'single' AND mp.price_variant IN ('nonfoil','foil'))
            )
          ORDER BY mp.external_product_id,
                   CASE WHEN mp.price_variant = 'nonfoil' THEN 0 WHEN mp.price_variant = 'foil' THEN 1 ELSE 0 END,
                   mp.id DESC
        )
        SELECT e.id,
               e.external_id,
               e.name,
               e.category,
               e.category_id,
               e.expansion_external_id,
               e.product_group,
               e.website_path,
               e.last_seen_at,
               e.last_seen_at AS snapshot_at,
               :game AS game,
               'available_verified' AS listing_status,
               CASE
                 WHEN COALESCE(al.variant_count,0)=1 AND al.linked_game_count=1 AND al.linked_game_id=e.game_id THEN 'verified'
                 WHEN COALESCE(al.variant_count,0)>1 THEN 'ambiguous'
                 WHEN COALESCE(al.variant_count,0)=1 AND al.linked_game_id<>e.game_id THEN 'conflict'
                 WHEN COALESCE(all_l.link_count,0)>0 THEN 'review_pending'
                 ELSE 'unverified'
               END AS identity_status,
               CASE WHEN COALESCE(al.variant_count,0)=1 AND al.linked_game_id=e.game_id THEN al.canonical_product_id ELSE NULL END AS canonical_product_id,
               CASE WHEN COALESCE(al.variant_count,0)=1 AND al.linked_game_id=e.game_id THEN al.product_variant_id ELSE NULL END AS product_variant_id,
               (
                 SELECT pi.url FROM product_images pi
                 WHERE pi.product_variant_id=al.product_variant_id
                 ORDER BY pi.is_primary DESC,pi.id ASC LIMIT 1
               ) AS primary_image_url,
               cp.currency,
               cp.price_variant,
               cp.price_low,
               cp.price_mid,
               cp.price_market,
               cp.price_last,
               cp.avg1,
               cp.avg7,
               cp.avg30,
               cp.as_of AS price_as_of
        FROM current_products e
        LEFT JOIN accepted_links al ON al.external_product_id=e.id
        LEFT JOIN all_links all_l ON all_l.external_product_id=e.id
        LEFT JOIN current_prices cp ON cp.external_product_id=e.id
        ORDER BY e.name ASC,e.external_id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(
        f"""
        WITH target_game AS (SELECT id FROM games WHERE slug=:game LIMIT 1),
        latest_catalog AS (
          SELECT MAX(e.last_seen_at) AS seen FROM external_catalog_products e JOIN target_game tg ON tg.id=e.game_id
          WHERE e.source='cardmarket'
        )
        SELECT COUNT(*) FROM external_catalog_products e
        JOIN target_game tg ON tg.id=e.game_id JOIN latest_catalog lc ON lc.seen=e.last_seen_at
        WHERE e.source='cardmarket' AND e.product_group=:group {extra_sql}
        """
    )

    try:
        with db.SessionLocal() as session:
            if session.execute(text("SELECT 1 FROM games WHERE slug=:game"), {"game": game}).scalar_one_or_none() is None:
                return jsonify({"error": "not_found", "detail": f"game '{game}' not found"}), 404
            rows = [dict(r) for r in session.execute(sql, params).mappings().all()]
            total = int(session.execute(count_sql, params).scalar_one())
    except SQLAlchemyError:
        return jsonify({"error": "market_current_products_unavailable"}), 503

    items = []
    for row in rows:
        for key in ("last_seen_at", "snapshot_at", "price_as_of"):
            if hasattr(row.get(key), "isoformat"):
                row[key] = row[key].isoformat()
        meaningful = any(_positive(row.get(k)) is not None for k in ("price_low","price_mid","price_market","price_last","avg1","avg7","avg30"))
        for key in ("price_low","price_mid","price_market","price_last","avg1","avg7","avg30"):
            row[key] = _positive(row.get(key)) if meaningful else None
        if not meaningful:
            row["currency"] = None
            row["price_variant"] = None
            row["price_as_of"] = None
        items.append(row)

    return jsonify({
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "price_contract": "latest_cardmarket_game_capture_only; missing/non-positive means unavailable",
        "identity_status_contract": {
            "verified": "Accepted exact link to one canonical product variant",
            "unverified": "No accepted canonical identity link",
            "review_pending": "A candidate link exists but is not accepted",
            "ambiguous": "Multiple accepted canonical variants conflict",
            "conflict": "Accepted link crosses game identity",
        },
    })
