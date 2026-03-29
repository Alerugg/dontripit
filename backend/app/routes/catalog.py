from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db

catalog_bp = Blueprint("catalog", __name__)

_RATE_LIMIT_BUCKETS = {}
_CACHE = {}


def _int_param(name: str, default: int, maximum: int) -> int:
    value = request.args.get(name, default=default, type=int)
    if value is None:
        return default
    return min(max(value, 0), maximum)


def _pagination(default_limit: int = 20, max_limit: int = 200) -> tuple[int, int]:
    return _int_param("limit", default_limit, max_limit), _int_param("offset", 0, 1_000_000)


def _json_error(error: str, detail: str, status: int):
    return jsonify({"error": error, "detail": detail}), status


def _variant_order_sql(column: str) -> str:
    return f"""
    CASE
      WHEN lower(COALESCE({column}, '')) IN ('default', 'base', '') THEN 0
      WHEN lower(COALESCE({column}, '')) LIKE '%parallel%' THEN 1
      WHEN lower(COALESCE({column}, '')) LIKE '%reverse%' THEN 2
      WHEN lower(COALESCE({column}, '')) LIKE '%full art%' THEN 3
      WHEN lower(COALESCE({column}, '')) LIKE '%alt art%' THEN 4
      WHEN lower(COALESCE({column}, '')) LIKE '%illustration%' THEN 5
      WHEN lower(COALESCE({column}, '')) LIKE '%holo%' THEN 6
      WHEN lower(COALESCE({column}, '')) LIKE '%foil%' THEN 7
      ELSE 8
    END
    """


def _collector_number_order_sql(column: str) -> str:
    return f"""
    CASE
      WHEN substring(COALESCE({column}, '') FROM '([0-9]+)$') IS NOT NULL
        THEN substring(COALESCE({column}, '') FROM '([0-9]+)$')::int
      WHEN substring(COALESCE({column}, '') FROM '([0-9]+)') IS NOT NULL
        THEN substring(COALESCE({column}, '') FROM '([0-9]+)')::int
      ELSE 2147483647
    END
    """


def _request_requires_game() -> bool:
    return request.path.startswith("/api/v1/")


def _get_game_slug(required: bool) -> tuple[str | None, tuple | None]:
    game = request.args.get("game", "").strip()
    if not game:
        if required:
            return None, _json_error("invalid_params", "game is required", 400)
        return None, None

    sql = text("SELECT slug FROM games WHERE slug = :slug")
    with db.SessionLocal() as session:
        row = session.execute(sql, {"slug": game}).mappings().first()
    if row is None:
        return None, _json_error("not_found", f"game '{game}' not found", 404)
    return game, None


@catalog_bp.get("/api/cards")
@catalog_bp.get("/api/v1/cards")
def list_cards():
    q = request.args.get("q", "").strip()
    limit, offset = _pagination()
    game, error = _get_game_slug(required=_request_requires_game())
    if error:
        return error

    where = []
    params = {"limit": limit, "offset": offset}
    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if q:
        where.append("LOWER(c.name) LIKE :q")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT c.id,
               c.name,
               g.slug AS game_slug,
               c.tcgdex_id,
               NULL AS scryfall_id,
               c.yugoprodeck_id AS yugioh_id,
               c.riftbound_id
        FROM cards c
        JOIN games g ON g.id = c.game_id
        {where_sql}
        ORDER BY c.name ASC, c.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    with db.SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return jsonify([dict(row) for row in rows])


@catalog_bp.get("/api/cards/<int:card_id>")
@catalog_bp.get("/api/v1/cards/<int:card_id>")
def get_card_detail(card_id: int):
    card_sql = text(
        """
        SELECT c.id,
               c.name,
               g.slug AS game_slug,
               g.slug AS game,
               c.tcgdex_id,
               NULL AS scryfall_id,
               c.yugoprodeck_id AS yugioh_id,
               c.riftbound_id,
               (
                 SELECT pi.url
                 FROM print_images pi
                 JOIN prints p ON p.id = pi.print_id
                 WHERE p.card_id = c.id
                 ORDER BY pi.is_primary DESC, pi.id ASC
                 LIMIT 1
               ) AS primary_image_url
        FROM cards c
        JOIN games g ON g.id = c.game_id
        WHERE c.id = :card_id
        """
    )
    prints_sql = text(
        f"""
        SELECT p.id,
               s.code AS set_code,
               s.name AS set_name,
               EXTRACT(YEAR FROM s.release_date)::int AS year,
               p.card_id,
               p.collector_number,
               p.language,
               p.rarity,
               p.is_foil,
               p.variant,
               COALESCE(
                 (
                   SELECT pi.url
                   FROM print_images pi
                   WHERE pi.print_id = p.id
                   ORDER BY pi.is_primary DESC, pi.id ASC
                   LIMIT 1
                 ),
                 NULL
               ) AS image_url,
               COALESCE(
                 (
                   SELECT pi.url
                   FROM print_images pi
                   WHERE pi.print_id = p.id
                   ORDER BY pi.is_primary DESC, pi.id ASC
                   LIMIT 1
                 ),
                 (
                   SELECT pi2.url
                   FROM print_images pi2
                   JOIN prints p2 ON p2.id = pi2.print_id
                   WHERE p2.card_id = p.card_id
                   ORDER BY pi2.is_primary DESC, pi2.id ASC
                   LIMIT 1
                 )
               ) AS primary_image_url
        FROM prints p
        JOIN sets s ON s.id = p.set_id
        WHERE p.card_id = :card_id
        ORDER BY s.release_date ASC NULLS LAST,
                 s.code ASC,
                 {_collector_number_order_sql('p.collector_number')} ASC,
                 lower(COALESCE(p.collector_number, '')) ASC,
                 {_variant_order_sql('p.variant')} ASC,
                 lower(COALESCE(p.variant, 'default')) ASC,
                 p.id ASC
        LIMIT 500
        """
    )
    sets_sql = text(
        """
        SELECT DISTINCT s.id, s.code, s.name, s.release_date
        FROM sets s
        JOIN prints p ON p.set_id = s.id
        WHERE p.card_id = :card_id
        ORDER BY s.release_date ASC NULLS LAST, s.name ASC, s.id ASC
        """
    )

    try:
        with db.SessionLocal() as session:
            card = session.execute(card_sql, {"card_id": card_id}).mappings().first()
            if card is None:
                return _json_error("not_found", f"card {card_id} not found", 404)
            prints = session.execute(prints_sql, {"card_id": card_id}).mappings().all()
            sets = session.execute(sets_sql, {"card_id": card_id}).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("card_detail_failed", str(error), 500)

    return jsonify(
        {
            "id": card["id"],
            "name": card["name"],
            "game_slug": card["game_slug"],
            "game": card["game"],
            "primary_image_url": card["primary_image_url"],
            "external_ids": {
                "tcgdex_id": card["tcgdex_id"],
                "scryfall_id": card["scryfall_id"],
                "yugioh_id": card["yugioh_id"],
                "riftbound_id": card["riftbound_id"],
            },
            "prints": [
                {
                    **dict(row),
                    "primary_image_url": row["primary_image_url"],
                }
                for row in prints
            ],
            "sets": [dict(row) for row in sets],
        }
    )


@catalog_bp.get("/api/sets")
@catalog_bp.get("/api/v1/sets")
def list_sets():
    q = request.args.get("q", "").strip()
    limit, offset = _pagination(default_limit=500, max_limit=1000)
    game, error = _get_game_slug(required=_request_requires_game())
    if error:
        return error

    where = []
    params = {"limit": limit, "offset": offset}

    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if q:
        where.append("(LOWER(s.name) LIKE :q OR LOWER(s.code) LIKE :q)")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = text(
        f"""
        SELECT
            s.id,
            s.code,
            s.name,
            g.slug AS game_slug,
            g.slug AS game,
            s.tcgdex_id,
            NULL AS scryfall_id,
            s.yugioh_id,
            s.riftbound_id,
            s.release_date,
            COUNT(DISTINCT p.card_id) AS card_count
        FROM sets s
        JOIN games g ON g.id = s.game_id
        LEFT JOIN prints p ON p.set_id = s.id
        {where_sql}
        GROUP BY
            s.id,
            s.code,
            s.name,
            g.slug,
            s.tcgdex_id,
            s.yugioh_id,
            s.riftbound_id,
            s.release_date
        ORDER BY
            s.release_date ASC NULLS LAST,
            LOWER(s.name) ASC,
            s.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, params).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("sets_query_failed", str(error), 500)

    return jsonify([dict(row) for row in rows])


@catalog_bp.get("/api/set-detail")
@catalog_bp.get("/api/v1/set-detail")
def get_set_detail():
    game, error = _get_game_slug(required=_request_requires_game())
    if error:
        return error

    set_code = request.args.get("set_code", "").strip()
    if not set_code:
        return _json_error("invalid_params", "set_code is required", 400)

    set_sql = text(
        f"""
        SELECT
            s.id,
            s.code,
            s.name,
            s.release_date,
            g.slug AS game,
            g.slug AS game_slug,
            COUNT(DISTINCT p.card_id) AS card_count,
            COUNT(DISTINCT p.id) AS print_count,
            MAX({_collector_number_order_sql('p.collector_number')}) AS collector_total
        FROM sets s
        JOIN games g ON g.id = s.game_id
        LEFT JOIN prints p ON p.set_id = s.id
        WHERE g.slug = :game
          AND LOWER(s.code) = LOWER(:set_code)
        GROUP BY s.id, s.code, s.name, s.release_date, g.slug
        """
    )

    prints_sql = text(
        f"""
        SELECT
            p.id,
            'print' AS type,
            p.id AS print_id,
            c.id AS card_id,
            c.name,
            c.name AS title,
            g.slug AS game,
            g.slug AS game_slug,
            s.code AS set_code,
            s.name AS set_name,
            EXTRACT(YEAR FROM s.release_date)::int AS year,
            p.collector_number,
            p.language,
            p.rarity,
            p.is_foil,
            p.variant,
            COALESCE(
              (
                SELECT pi.url
                FROM print_images pi
                WHERE pi.print_id = p.id
                ORDER BY pi.is_primary DESC, pi.id ASC
                LIMIT 1
              ),
              NULL
            ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = s.game_id
        WHERE g.slug = :game
          AND LOWER(s.code) = LOWER(:set_code)
        ORDER BY
            {_collector_number_order_sql('p.collector_number')} ASC,
            LOWER(COALESCE(p.collector_number, '')) ASC,
            {_variant_order_sql('p.variant')} ASC,
            LOWER(COALESCE(p.variant, 'default')) ASC,
            LOWER(c.name) ASC,
            p.id ASC
        LIMIT 2000
        """
    )

    try:
        with db.SessionLocal() as session:
            set_row = session.execute(set_sql, {"game": game, "set_code": set_code}).mappings().first()
            if set_row is None:
                return _json_error("not_found", f"set '{set_code}' not found in game '{game}'", 404)

            prints = session.execute(prints_sql, {"game": game, "set_code": set_code}).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("set_detail_failed", str(error), 500)

    return jsonify(
        {
            "set": dict(set_row),
            "cards": [dict(row) for row in prints],
        }
    )


@catalog_bp.get("/api/prints")
@catalog_bp.get("/api/v1/prints")
def list_prints():
    q = request.args.get("q", "").strip()
    set_code = request.args.get("set_code", "").strip()
    card_id = request.args.get("card_id", type=int)
    limit, offset = _pagination()
    game, error = _get_game_slug(required=_request_requires_game())
    if error:
        return error

    q_norm = q.lower()
    where = []
    params = {
        "limit": limit,
        "offset": offset,
    }

    if game:
        where.append("g.slug = :game")
        params["game"] = game

    if set_code:
        where.append("LOWER(s.code) = :set_code")
        params["set_code"] = set_code.lower()

    if card_id is not None:
        where.append("p.card_id = :card_id")
        params["card_id"] = card_id

    if q:
        where.append("LOWER(c.name) LIKE :q_contains")
        params["q_contains"] = f"%{q_norm}%"
        params["q_exact"] = q_norm
        params["q_prefix"] = f"{q_norm}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""

    sql = text(
        f"""
        SELECT
            p.id,
            p.card_id,
            c.name AS title,
            c.name AS name,
            g.slug AS game,
            s.id AS set_id,
            s.code AS set_code,
            s.name AS set_name,
            s.release_date,
            p.collector_number,
            p.language,
            p.rarity,
            p.is_foil,
            p.variant,
            CASE
                WHEN p.variant IS NOT NULL AND lower(p.variant) NOT IN ('', 'default', 'base') THEN p.variant
                WHEN p.is_foil IS TRUE THEN 'Holo'
                ELSE 'Normal'
            END AS finish_label,
            (
                SELECT pi.url
                FROM print_images pi
                WHERE pi.print_id = p.id
                ORDER BY pi.is_primary DESC, pi.id ASC
                LIMIT 1
            ) AS image_url,
            COALESCE(
                (
                    SELECT pi.url
                    FROM print_images pi
                    WHERE pi.print_id = p.id
                    ORDER BY pi.is_primary DESC, pi.id ASC
                    LIMIT 1
                ),
                (
                    SELECT pi2.url
                    FROM print_images pi2
                    JOIN prints p2 ON p2.id = pi2.print_id
                    WHERE p2.card_id = p.card_id
                    ORDER BY pi2.is_primary DESC, pi2.id ASC
                    LIMIT 1
                )
            ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = s.game_id
        {where_sql}
        ORDER BY
            CASE
                WHEN :q_exact IS NOT NULL AND LOWER(c.name) = :q_exact THEN 0
                WHEN :q_prefix IS NOT NULL AND LOWER(c.name) LIKE :q_prefix THEN 1
                WHEN :q_contains IS NOT NULL AND LOWER(c.name) LIKE :q_contains THEN 2
                ELSE 3
            END,
            s.release_date DESC NULLS LAST,
            lower(s.name) ASC,
            CASE
                WHEN regexp_replace(COALESCE(p.collector_number, ''), '[^0-9]', '', 'g') <> ''
                THEN CAST(regexp_replace(p.collector_number, '[^0-9]', '', 'g') AS INTEGER)
                ELSE 999999
            END ASC,
            lower(COALESCE(p.collector_number, '')) ASC,
            p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    try:
        with db.SessionLocal() as session:
            rows = session.execute(
                sql,
                {
                    **params,
                    "q_exact": q_norm if q else None,
                    "q_prefix": f"{q_norm}%" if q else None,
                    "q_contains": f"%{q_norm}%" if q else None,
                },
            ).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("prints_query_failed", str(error), 500)

    items = []
    for row in rows:
        item = dict(row)
        if hasattr(item.get("release_date"), "isoformat"):
            item["release_date"] = item["release_date"].isoformat()
        items.append(item)

    return jsonify(items)


@catalog_bp.get("/api/products")
@catalog_bp.get("/api/v1/products")
def list_products():
    game = request.args.get("game", "").strip()
    set_code = request.args.get("set_code", "").strip()
    product_type = request.args.get("type", "").strip()
    q = request.args.get("q", "").strip()
    limit = _int_param("limit", 20, 100)
    offset = _int_param("offset", 0, 100000)

    where = []
    params = {"limit": limit, "offset": offset}
    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if set_code:
        where.append("s.code = :set_code")
        params["set_code"] = set_code
    if product_type:
        where.append("p.product_type = :product_type")
        params["product_type"] = product_type
    if q:
        where.append("LOWER(p.name) LIKE :q")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT p.id,
               p.product_type,
               p.name,
               p.release_date,
               g.slug AS game,
               s.code AS set_code,
               COALESCE(v.variant_count, 0) AS variant_count,
               i.primary_image_url
        FROM products p
        JOIN games g ON g.id = p.game_id
        LEFT JOIN sets s ON s.id = p.set_id
        LEFT JOIN (
            SELECT product_id, COUNT(*) AS variant_count
            FROM product_variants
            GROUP BY product_id
        ) v ON v.product_id = p.id
        LEFT JOIN (
            SELECT pv.product_id, MIN(pi.url) AS primary_image_url
            FROM product_variants pv
            JOIN product_images pi ON pi.product_variant_id = pv.id
            WHERE pi.is_primary = true
            GROUP BY pv.product_id
        ) i ON i.product_id = p.id
        {where_sql}
        ORDER BY p.name ASC, p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    count_sql = text(
        f"""
        SELECT COUNT(*)
        FROM products p
        JOIN games g ON g.id = p.game_id
        LEFT JOIN sets s ON s.id = p.set_id
        {where_sql}
        """
    )

    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, params).mappings().all()
            total = session.execute(count_sql, params).scalar_one()
    except SQLAlchemyError as error:
        return _json_error("products_query_failed", str(error), 500)

    items = []
    for row in rows:
        item = dict(row)
        if hasattr(item.get("release_date"), "isoformat"):
            item["release_date"] = item["release_date"].isoformat()
        items.append(item)

    return jsonify({"items": items, "limit": limit, "offset": offset, "total": total})


@catalog_bp.get("/api/products/<int:product_id>")
@catalog_bp.get("/api/v1/products/<int:product_id>")
def product_detail(product_id: int):
    product_sql = text(
        """
        SELECT p.id,
               g.slug AS game,
               s.code AS set_code,
               p.product_type,
               p.name,
               p.release_date
        FROM products p
        JOIN games g ON g.id = p.game_id
        LEFT JOIN sets s ON s.id = p.set_id
        WHERE p.id = :product_id
        """
    )
    variants_sql = text(
        """
        SELECT id, product_id, language, region, packaging, sku
        FROM product_variants
        WHERE product_id = :product_id
        ORDER BY id ASC
        """
    )
    images_sql = text(
        """
        SELECT pi.id, pi.product_variant_id, pi.url, pi.is_primary, pi.source
        FROM product_images pi
        JOIN product_variants pv ON pv.id = pi.product_variant_id
        WHERE pv.product_id = :product_id
        ORDER BY pi.product_variant_id ASC, pi.is_primary DESC, pi.id ASC
        """
    )
    identifiers_sql = text(
        """
        SELECT pid.id, pid.product_variant_id, pid.source, pid.external_id
        FROM product_identifiers pid
        JOIN product_variants pv ON pv.id = pid.product_variant_id
        WHERE pv.product_id = :product_id
        ORDER BY pid.product_variant_id ASC, pid.source ASC, pid.id ASC
        """
    )

    try:
        with db.SessionLocal() as session:
            product = session.execute(product_sql, {"product_id": product_id}).mappings().first()
            if product is None:
                return _json_error("not_found", f"product {product_id} not found", 404)
            variants = session.execute(variants_sql, {"product_id": product_id}).mappings().all()
            images = session.execute(images_sql, {"product_id": product_id}).mappings().all()
            identifiers = session.execute(identifiers_sql, {"product_id": product_id}).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("product_detail_failed", str(error), 500)

    product_payload = dict(product)
    if hasattr(product_payload.get("release_date"), "isoformat"):
        product_payload["release_date"] = product_payload["release_date"].isoformat()

    return jsonify(
        {
            "product": product_payload,
            "variants": [dict(row) for row in variants],
            "images": [dict(row) for row in images],
            "identifiers": [dict(row) for row in identifiers],
        }
    )


@catalog_bp.get("/api/product-variants")
@catalog_bp.get("/api/v1/product-variants")
def list_product_variants():
    product_id = request.args.get("product_id", type=int)
    if product_id is None:
        return _json_error("invalid_params", "product_id is required", 400)

    sql = text(
        """
        SELECT id, product_id, language, region, packaging, sku
        FROM product_variants
        WHERE product_id = :product_id
        ORDER BY id ASC
        """
    )

    with db.SessionLocal() as session:
        rows = session.execute(sql, {"product_id": product_id}).mappings().all()

    return jsonify([dict(row) for row in rows])


@catalog_bp.get("/api/prints/<int:print_id>")
@catalog_bp.get("/api/v1/prints/<int:print_id>")
def get_print_detail(print_id: int):
    sql = text(
        """
        SELECT p.id,
               g.slug AS game,
               c.name AS title,
               p.collector_number,
               p.language,
               p.rarity,
               p.is_foil,
               p.variant,
               p.scryfall_id,
               p.tcgdex_id,
               p.yugioh_id,
               p.riftbound_id,
               c.id AS card_id,
               c.name AS card_name,
               s.id AS set_id,
               s.code AS set_code,
               s.name AS set_name,
               EXTRACT(YEAR FROM s.release_date)::int AS year,
               (
                 SELECT pi.url
                 FROM print_images pi
                 WHERE pi.print_id = p.id
                 ORDER BY pi.is_primary DESC, pi.id ASC
                 LIMIT 1
               ) AS image_url,
               COALESCE(
                 (
                   SELECT pi.url
                   FROM print_images pi
                   WHERE pi.print_id = p.id
                   ORDER BY pi.is_primary DESC, pi.id ASC
                   LIMIT 1
                 ),
                 (
                   SELECT pi2.url
                   FROM print_images pi2
                   JOIN prints p2 ON p2.id = pi2.print_id
                   WHERE p2.card_id = p.card_id
                   ORDER BY pi2.is_primary DESC, pi2.id ASC
                   LIMIT 1
                 )
               ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = s.game_id
        WHERE p.id = :print_id
        """
    )

    images_sql = text(
        """
        SELECT url, is_primary, source
        FROM print_images
        WHERE print_id = :print_id
        ORDER BY is_primary DESC, id ASC
        """
    )
    identifiers_sql = text(
        """
        SELECT source, external_id
        FROM print_identifiers
        WHERE print_id = :print_id
        ORDER BY source ASC, id ASC
        """
    )

    try:
        with db.SessionLocal() as session:
            row = session.execute(sql, {"print_id": print_id}).mappings().first()
            if row is None:
                return _json_error("not_found", f"print {print_id} not found", 404)
            images = session.execute(images_sql, {"print_id": print_id}).mappings().all()
            identifiers = session.execute(identifiers_sql, {"print_id": print_id}).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("print_detail_failed", str(error), 500)

    payload = {
        "id": row["id"],
        "game": row["game"],
        "title": row["title"],
        "set_code": row["set_code"],
        "set_name": row["set_name"],
        "year": row["year"],
        "collector_number": row["collector_number"],
        "language": row["language"],
        "rarity": row["rarity"],
        "is_foil": row["is_foil"],
        "variant": row["variant"],
        "primary_image_url": row["primary_image_url"],
        "set": {"id": row["set_id"], "code": row["set_code"], "name": row["set_name"]},
        "card": {"id": row["card_id"], "name": row["card_name"]},
        "image_url": row["image_url"],
        "external_ids": {
            "tcgdex_id": row["tcgdex_id"],
            "scryfall_id": row["scryfall_id"],
            "yugioh_id": row["yugioh_id"],
            "riftbound_id": row["riftbound_id"],
        },
    }

    payload["print"] = {
        "id": payload["id"],
        "collector_number": payload["collector_number"],
        "language": payload["language"],
        "rarity": payload["rarity"],
        "is_foil": payload["is_foil"],
        "variant": payload["variant"],
    }
    payload["images"] = [dict(image) for image in images]
    payload["identifiers"] = [dict(identifier) for identifier in identifiers]
    return jsonify(payload)