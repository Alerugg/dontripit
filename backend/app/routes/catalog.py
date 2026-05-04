import re

from flask import Blueprint, jsonify, request
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app import db

catalog_bp = Blueprint("catalog", __name__)

_RATE_LIMIT_BUCKETS = {}
_CACHE = {}
_ONEPIECE_COLLECTOR_SET_CODE_RE = re.compile(r"\b(OP|ST|EB)[\s\-_]?(\d{1,2})\b", re.IGNORECASE)
_ONEPIECE_CANONICAL_CODE_RE = re.compile(r"^(?:op|st|eb|prb|p)-\d{1,3}$", re.IGNORECASE)
def _is_numeric_like(value: str | None) -> bool:
    return bool(re.fullmatch(r"\d+", str(value or "").strip()))


def _normalize_onepiece_set_row(row: dict) -> dict:
    """Normalize degraded legacy One Piece labels without collapsing set identities."""

    code = str(row.get("code") or "").strip()
    name = str(row.get("name") or "").strip()

    if _is_numeric_like(code) and (not name or _is_numeric_like(name)):
        row["name"] = f"Set #{row.get('id')}"

    return row


def _is_onepiece_canonical_set_code(code: str | None) -> bool:
    return bool(_ONEPIECE_CANONICAL_CODE_RE.fullmatch(str(code or "").strip()))


def _extract_onepiece_commercial_code_from_collector(collector_number: str | None) -> str:
    raw = str(collector_number or "").strip()
    if not raw:
        return ""
    match = _ONEPIECE_COLLECTOR_SET_CODE_RE.search(raw)
    if not match:
        return ""
    prefix, number = match.groups()
    return f"{prefix.lower()}-{int(number):02d}"


def _apply_onepiece_set_name_mapping(rows: list[dict]) -> list[dict]:
    if not rows:
        return rows

    set_ids = [int(row["id"]) for row in rows if row.get("id") is not None]
    if not set_ids:
        return rows

    collectors_sql = text(
        """
        SELECT p.set_id, p.collector_number
        FROM prints p
        WHERE p.set_id IN :set_ids
          AND trim(COALESCE(p.collector_number, '')) <> ''
        """
    ).bindparams(bindparam("set_ids", expanding=True))
    canonical_sets_sql = text(
        """
        SELECT lower(s.code) AS code,
               COUNT(*) AS code_rows,
               MAX(trim(COALESCE(s.name, ''))) AS canonical_name
        FROM sets s
        JOIN games g ON g.id = s.game_id
        WHERE g.slug = 'onepiece'
          AND lower(s.code) IN :codes
        GROUP BY lower(s.code)
        HAVING COUNT(*) = 1
        """
    ).bindparams(bindparam("codes", expanding=True))

    with db.SessionLocal() as session:
        collector_rows = session.execute(collectors_sql, {"set_ids": set_ids}).mappings().all()

        inferred_codes_by_set_id: dict[int, set[str]] = {}
        inferred_collectors_by_set_id: dict[int, int] = {}
        non_empty_collectors_by_set_id: dict[int, int] = {}
        for collector_row in collector_rows:
            set_id = int(collector_row["set_id"])
            non_empty_collectors_by_set_id[set_id] = non_empty_collectors_by_set_id.get(set_id, 0) + 1
            inferred = _extract_onepiece_commercial_code_from_collector(collector_row.get("collector_number"))
            if not inferred:
                continue
            inferred_collectors_by_set_id[set_id] = inferred_collectors_by_set_id.get(set_id, 0) + 1
            inferred_codes_by_set_id.setdefault(set_id, set()).add(inferred)

        unique_inferred_by_set_id = {
            set_id: next(iter(codes))
            for set_id, codes in inferred_codes_by_set_id.items()
            if len(codes) == 1
            and inferred_collectors_by_set_id.get(set_id, 0) == non_empty_collectors_by_set_id.get(set_id, 0)
        }

        inferred_codes = sorted(set(unique_inferred_by_set_id.values()))
        canonical_by_code: dict[str, str] = {}
        if inferred_codes:
            canonical_rows = session.execute(canonical_sets_sql, {"codes": inferred_codes}).mappings().all()
            for canonical_row in canonical_rows:
                code = str(canonical_row.get("code") or "").strip().lower()
                if not code:
                    continue
                name = str(canonical_row.get("canonical_name") or "").strip()
                canonical_by_code[code] = name or code.upper()

    mapped_rows: list[dict] = []
    for row in rows:
        original_name = str(row.get("name") or "").strip()
        original_code = str(row.get("code") or "").strip()
        is_degraded_numeric = _is_numeric_like(original_code) and (not original_name or _is_numeric_like(original_name))
        normalized = _normalize_onepiece_set_row(dict(row))
        set_id = int(normalized["id"])
        inferred_code = unique_inferred_by_set_id.get(set_id)
        normalized["onepiece_set_classification"] = "canonical" if _is_onepiece_canonical_set_code(original_code) else "legacy_mixed_ambiguous"
        if inferred_code and inferred_code in canonical_by_code and is_degraded_numeric:
            normalized["name"] = canonical_by_code[inferred_code]
            normalized["onepiece_set_classification"] = "canonical"
        mapped_rows.append(normalized)
    return mapped_rows


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
      WHEN lower(COALESCE({column}, '')) LIKE 'r%' THEN 2
      ELSE 3
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
        ORDER BY s.code ASC,
                 p.collector_number ASC,
                 {_variant_order_sql('p.variant')} ASC,
                 lower(COALESCE(p.variant, 'default')) ASC,
                 p.id ASC
        LIMIT 50
        """
    )
    sets_sql = text(
        """
        SELECT DISTINCT s.id, s.code, s.name
        FROM sets s
        JOIN prints p ON p.set_id = s.id
        WHERE p.card_id = :card_id
        ORDER BY s.name ASC, s.id ASC
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


def _catalog_print_resolve_values() -> tuple[list[str] | None, tuple | None]:
    payload = request.get_json(silent=True) or {}

    if not isinstance(payload, dict):
        return None, _json_error("invalid_request", "request body must be a JSON object", 400)

    raw_values = []

    if "print_id" in payload:
        raw_values.append(payload.get("print_id"))

    if "print_ids" in payload:
        if not isinstance(payload.get("print_ids"), list):
            return None, _json_error("invalid_request", "print_ids must be a list", 400)
        raw_values.extend(payload.get("print_ids") or [])

    values = []
    seen = set()

    for raw_value in raw_values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        values.append(value)
        seen.add(value)

    if not values:
        return None, _json_error("invalid_request", "print_id or print_ids is required", 400)

    if len(values) > 100:
        return None, _json_error("invalid_request", "a maximum of 100 print ids can be resolved at once", 400)

    return values, None


def _catalog_print_payload(row: dict, identifiers_by_print_id: dict[int, dict[str, str]]) -> dict:
    print_id = int(row["print_id"])
    external_ids = {
        "scryfall_id": row["scryfall_id"],
        "tcgdex_id": row["tcgdex_id"],
        "yugioh_id": row["yugioh_id"],
        "riftbound_id": row["riftbound_id"],
    }

    identifiers = identifiers_by_print_id.get(print_id) or {}
    if identifiers:
        external_ids["identifiers"] = identifiers

    return {
        "print_id": str(print_id),
        "id": print_id,
        "game": row["game_slug"],
        "game_slug": row["game_slug"],
        "game_name": row["game_name"],
        "card_id": row["card_id"],
        "card_name": row["card_name"],
        "set_id": row["set_id"],
        "set_code": row["set_code"],
        "set_name": row["set_name"],
        "collector_number": row["collector_number"],
        "language": row["language"],
        "rarity": row["rarity"],
        "is_foil": row["is_foil"],
        "variant": row["variant"],
        "image_url": row["primary_image_url"],
        "primary_image_url": row["primary_image_url"],
        "print_key": row["print_key"],
        "external_ids": external_ids,
    }


@catalog_bp.post("/api/prints/resolve")
@catalog_bp.post("/api/catalog/prints/resolve")
@catalog_bp.post("/api/v1/prints/resolve")
def resolve_catalog_prints():
    values, error = _catalog_print_resolve_values()
    if error:
        return error

    numeric_ids = [int(value) for value in values if re.fullmatch(r"\d+", value)]
    if not numeric_ids:
        numeric_ids = [-1]

    text_values = values or ["__never_match__"]

    prints_sql = text(
        """
        SELECT p.id AS print_id,
               p.print_key,
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
               g.slug AS game_slug,
               g.name AS game_name,
               (
                 SELECT pi.url
                 FROM print_images pi
                 WHERE pi.print_id = p.id
                 ORDER BY pi.is_primary DESC, pi.id ASC
                 LIMIT 1
               ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = c.game_id
        WHERE p.id IN :numeric_ids
           OR p.print_key IN :text_values
           OR p.scryfall_id IN :text_values
           OR p.tcgdex_id IN :text_values
           OR p.yugioh_id IN :text_values
           OR p.riftbound_id IN :text_values
           OR EXISTS (
             SELECT 1
             FROM print_identifiers pi
             WHERE pi.print_id = p.id
               AND pi.external_id IN :text_values
           )
        ORDER BY p.id ASC
        """
    ).bindparams(
        bindparam("numeric_ids", expanding=True),
        bindparam("text_values", expanding=True),
    )

    identifiers_sql = text(
        """
        SELECT print_id, source, external_id
        FROM print_identifiers
        WHERE print_id IN :print_ids
        ORDER BY source ASC, id ASC
        """
    ).bindparams(bindparam("print_ids", expanding=True))

    try:
        with db.SessionLocal() as session:
            rows = session.execute(
                prints_sql,
                {
                    "numeric_ids": numeric_ids,
                    "text_values": text_values,
                },
            ).mappings().all()

            print_ids = [int(row["print_id"]) for row in rows]
            identifier_rows = []
            if print_ids:
                identifier_rows = session.execute(
                    identifiers_sql,
                    {"print_ids": print_ids},
                ).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("print_resolve_failed", str(error), 500)

    identifiers_by_print_id: dict[int, dict[str, str]] = {}
    for identifier_row in identifier_rows:
        print_id = int(identifier_row["print_id"])
        source = str(identifier_row["source"] or "").strip()
        external_id = str(identifier_row["external_id"] or "").strip()
        if source and external_id:
            identifiers_by_print_id.setdefault(print_id, {})[source] = external_id

    row_by_value: dict[str, dict] = {}
    for row in rows:
        row_dict = dict(row)
        candidate_values = [
            str(row_dict.get("print_id") or "").strip(),
            str(row_dict.get("print_key") or "").strip(),
            str(row_dict.get("scryfall_id") or "").strip(),
            str(row_dict.get("tcgdex_id") or "").strip(),
            str(row_dict.get("yugioh_id") or "").strip(),
            str(row_dict.get("riftbound_id") or "").strip(),
        ]

        for candidate_value in candidate_values:
            if candidate_value:
                row_by_value.setdefault(candidate_value, row_dict)

    for identifier_row in identifier_rows:
        external_id = str(identifier_row["external_id"] or "").strip()
        if not external_id:
            continue

        matching_row = next(
            (dict(row) for row in rows if int(row["print_id"]) == int(identifier_row["print_id"])),
            None,
        )
        if matching_row:
            row_by_value.setdefault(external_id, matching_row)

    resolved = []
    for value in values:
        row = row_by_value.get(value)

        if not row:
            resolved.append(
                {
                    "query": value,
                    "found": False,
                    "print_id": value,
                    "catalog": None,
                }
            )
            continue

        catalog = _catalog_print_payload(row, identifiers_by_print_id)
        resolved.append(
            {
                "query": value,
                "found": True,
                "print_id": catalog["print_id"],
                "catalog": catalog,
            }
        )

    return jsonify({"prints": resolved})


@catalog_bp.get("/api/sets")
@catalog_bp.get("/api/v1/sets")
def list_sets():
    q = request.args.get("q", "").strip()
    include_legacy_ambiguous = str(request.args.get("include_legacy_ambiguous", "")).strip().lower() in {"1", "true", "yes"}
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
        where.append("(LOWER(s.name) LIKE :q OR LOWER(s.code) LIKE :q)")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT s.id,
               s.code,
               CASE
                 WHEN trim(COALESCE(s.name, '')) = '' THEN s.code
                 ELSE s.name
               END AS name,
               g.slug AS game_slug,
               s.tcgdex_id,
               NULL AS scryfall_id,
               s.yugioh_id,
               s.riftbound_id,
               (
                 SELECT p.collector_number
                 FROM prints p
                 WHERE p.set_id = s.id
                   AND trim(COALESCE(p.collector_number, '')) <> ''
                 ORDER BY p.id ASC
                 LIMIT 1
               ) AS sample_collector_number,
               COALESCE(set_card_counts.card_count, 0) AS card_count
        FROM sets s
        JOIN games g ON g.id = s.game_id
        LEFT JOIN (
            SELECT p.set_id, COUNT(DISTINCT p.card_id) AS card_count
            FROM prints p
            GROUP BY p.set_id
        ) set_card_counts ON set_card_counts.set_id = s.id
        {where_sql}
        ORDER BY s.name ASC, s.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    try:
        with db.SessionLocal() as session:
            rows = session.execute(sql, params).mappings().all()
    except SQLAlchemyError as error:
        return _json_error("sets_query_failed", str(error), 500)

    normalized_rows = [dict(row) for row in rows]
    if game == "onepiece":
        normalized_rows = _apply_onepiece_set_name_mapping(normalized_rows)
        if not q and not include_legacy_ambiguous:
            normalized_rows = [
                row for row in normalized_rows if row.get("onepiece_set_classification") != "legacy_mixed_ambiguous"
            ]

    payload = []
    for row in normalized_rows:
        row.pop("sample_collector_number", None)
        payload.append(row)

    return jsonify(payload)


@catalog_bp.get("/api/prints")
@catalog_bp.get("/api/v1/prints")
def list_prints():
    set_code = request.args.get("set_code", "").strip()
    card_id = request.args.get("card_id", type=int)
    limit, offset = _pagination()
    game, error = _get_game_slug(required=_request_requires_game())
    if error:
        return error

    where = []
    params = {"limit": limit, "offset": offset}
    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if set_code:
        where.append("LOWER(s.code) = :set_code")
        params["set_code"] = set_code.lower()
    if card_id is not None:
        where.append("p.card_id = :card_id")
        params["card_id"] = card_id

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT p.id,
               s.code AS set_code,
               p.card_id,
               p.collector_number,
               p.language,
               p.rarity,
               p.is_foil,
               p.variant,
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
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = s.game_id
        {where_sql}
        ORDER BY s.code ASC, p.collector_number ASC, p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )

    with db.SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    return jsonify([dict(row) for row in rows])


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
