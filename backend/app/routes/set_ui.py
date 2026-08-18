import re

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.search_v2.market_ordering import current_cardmarket_price_join, normalize_search_sort, print_order_sql

set_ui_bp = Blueprint("set_ui", __name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalized_release_code(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _normalized_region(value: object) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if len(raw) > 16 or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", raw):
        raise ValueError("invalid set_region")
    return raw


def _release_codes(row: dict) -> set[str]:
    candidates = [row.get("code")]
    candidates.extend(re.findall(r"\[([^\]]+)\]", str(row.get("name") or "")))
    return {value for raw in candidates if (value := _normalized_release_code(raw))}


def _resolve_catalog_release(session, *, game: str, requested_code: str, requested_region: str | None = None) -> dict | None:
    compact = _normalized_release_code(requested_code)
    if not compact:
        return None

    rows = session.execute(
        text(
            """
            SELECT cr.id, cr.source, cr.external_id, cr.name, cr.code,
                   cr.release_type, cr.release_date, cr.language, cr.region
            FROM catalog_releases cr
            JOIN games g ON g.id = cr.game_id
            WHERE g.slug = :game
              AND (:region IS NULL OR lower(COALESCE(cr.region, 'global')) = :region)
            ORDER BY cr.id ASC
            """
        ),
        {"game": game, "region": requested_region},
    ).mappings().all()

    matches = [dict(row) for row in rows if compact in _release_codes(dict(row))]
    if not matches:
        return None

    def trust(row: dict) -> tuple[int, int]:
        source = str(row.get("source") or "").lower()
        explicit_code = int(_normalized_release_code(row.get("code")) == compact)
        official = int("official" in source)
        return official, explicit_code

    best = max(trust(row) for row in matches)
    finalists = [row for row in matches if trust(row) == best]
    if len(finalists) != 1:
        return {"ambiguous": True, "matches": finalists}
    return finalists[0]


def _resolve_canonical_set(session, *, game: str, requested_code: str, requested_region: str | None = None) -> dict | None:
    rows = session.execute(
        text(
            """
            SELECT s.id, s.code, s.name, s.region
            FROM sets s
            JOIN games g ON g.id = s.game_id
            WHERE g.slug = :game
              AND lower(s.code) = :set_code
              AND (:region IS NULL OR lower(s.region) = :region)
            ORDER BY CASE WHEN lower(s.region) = 'global' THEN 0 ELSE 1 END, s.id ASC
            """
        ),
        {"game": game, "set_code": requested_code.lower(), "region": requested_region},
    ).mappings().all()
    matches = [dict(row) for row in rows]
    if not matches:
        return None
    if requested_region:
        if len(matches) != 1:
            return {"ambiguous": True, "matches": matches}
        return matches[0]

    # Backwards compatibility: historical callers without a region keep
    # resolving the pre-existing global/TCG set. Never UNION its prints with a
    # regional OCG set that happens to share the same real code.
    global_matches = [row for row in matches if str(row.get("region") or "").lower() == "global"]
    if len(global_matches) == 1:
        return global_matches[0]
    if len(matches) == 1:
        return matches[0]
    return {"ambiguous": True, "matches": matches}


def _order_sql(sort: str) -> str:
    default = (
        "COALESCE(NULLIF(substring(p.collector_number from '([0-9]+)[^0-9]*$'), '')::bigint, 9223372036854775807) ASC, "
        "lower(COALESCE(p.collector_number,'')) ASC, "
        "CASE WHEN COALESCE(p.variant,'default')='default' THEN 0 ELSE 1 END ASC, "
        "lower(COALESCE(p.variant,'')) ASC, p.id ASC"
    )
    return print_order_sql(sort, default=default)


@set_ui_bp.get("/api/v1/set-ui/prints")
def list_set_ui_prints():
    """Paginated exact-Print checklist for a canonical set or official release."""
    game = str(request.args.get("game") or "").strip().lower()
    set_code = str(request.args.get("set_code") or "").strip()
    if not game or not set_code:
        return jsonify({"error": "invalid_params", "detail": "game and set_code are required"}), 400
    try:
        set_region = _normalized_region(request.args.get("set_region"))
    except ValueError:
        return jsonify({"error": "invalid_params", "detail": "invalid set_region"}), 400

    limit = _bounded_int(request.args.get("limit"), default=36, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=100_000)
    q = str(request.args.get("q") or "").strip().lower()[:200]
    try:
        sort = normalize_search_sort(request.args.get("sort") or "number_asc")
    except ValueError:
        return jsonify({"error": "invalid_params", "detail": "unsupported sort"}), 400
    has_price = _bool(request.args.get("has_price"))

    q_clause = """
      AND (
        lower(c.name) LIKE :q
        OR lower(COALESCE(p.collector_number,'')) LIKE :q
        OR lower(COALESCE(p.rarity,'')) LIKE :q
        OR lower(COALESCE(p.variant,'')) LIKE :q
        OR lower(COALESCE(p.language,'')) LIKE :q
      )
    """ if q else ""
    price_clause = " AND cm.cardmarket_price IS NOT NULL" if has_price else ""
    market_join = current_cardmarket_price_join(print_id="p.id", game_slug=game)
    order_sql = _order_sql(sort)

    try:
        with db.SessionLocal() as session:
            release = _resolve_catalog_release(
                session,
                game=game,
                requested_code=set_code,
                requested_region=set_region,
            )
            if release and release.get("ambiguous"):
                return jsonify({
                    "error": "release_code_ambiguous",
                    "game": game,
                    "set_code": set_code,
                    "set_region": set_region,
                    "matches": [
                        {
                            "id": row["id"],
                            "source": row["source"],
                            "external_id": row["external_id"],
                            "name": row["name"],
                            "code": row["code"],
                            "region": row.get("region"),
                        }
                        for row in release["matches"]
                    ],
                }), 409

            canonical_set = None
            if not release:
                canonical_set = _resolve_canonical_set(
                    session,
                    game=game,
                    requested_code=set_code,
                    requested_region=set_region,
                )
                if canonical_set and canonical_set.get("ambiguous"):
                    return jsonify({
                        "error": "set_code_ambiguous",
                        "game": game,
                        "set_code": set_code,
                        "set_region": set_region,
                        "matches": [
                            {"id": row["id"], "name": row["name"], "code": row["code"], "region": row["region"]}
                            for row in canonical_set["matches"]
                        ],
                    }), 409
                if canonical_set is None:
                    return jsonify({
                        "error": "set_not_found",
                        "game": game,
                        "set_code": set_code,
                        "set_region": set_region,
                    }), 404

            common_select = """
                SELECT p.id, p.id AS print_id, p.card_id,
                       c.name AS card_name, c.name AS name,
                       s.code AS set_code, s.name AS set_name, s.region AS set_region,
                       p.collector_number, p.language, p.rarity, p.is_foil, p.variant,
                       cm.cardmarket_external_product_id, cm.cardmarket_id_product,
                       cm.cardmarket_product_name, cm.cardmarket_website_path,
                       cm.cardmarket_price, cm.cardmarket_currency, cm.cardmarket_as_of,
                       COALESCE((
                         SELECT jsonb_agg(
                           jsonb_build_object(
                             'id', cr.id,
                             'source', cr.source,
                             'external_id', cr.external_id,
                             'name', cr.name,
                             'code', cr.code,
                             'release_type', cr.release_type,
                             'release_date', cr.release_date,
                             'language', cr.language,
                             'region', cr.region
                           ) ORDER BY cr.release_date NULLS LAST, cr.id
                         )
                         FROM print_releases pr2
                         JOIN catalog_releases cr ON cr.id=pr2.release_id
                         WHERE pr2.print_id=p.id
                       ), '[]'::jsonb) AS physical_releases,
                       (SELECT pi.url FROM print_images pi WHERE pi.print_id=p.id ORDER BY pi.is_primary DESC, pi.id ASC LIMIT 1) AS primary_image_url
            """

            if release:
                base_from = f"""
                    FROM print_releases pr
                    JOIN prints p ON p.id=pr.print_id
                    JOIN cards c ON c.id=p.card_id
                    JOIN sets s ON s.id=p.set_id
                    {market_join}
                    WHERE pr.release_id=:release_id
                """
                params = {"release_id": release["id"], "q": f"%{q}%", "limit": limit, "offset": offset}
                scope = {
                    "type": "release",
                    "release_id": release["id"],
                    "release_source": release["source"],
                    "release_external_id": release["external_id"],
                    "release_name": release["name"],
                    "release_code": release["code"],
                    "release_region": release.get("region"),
                }
            else:
                base_from = f"""
                    FROM prints p
                    JOIN cards c ON c.id=p.card_id
                    JOIN sets s ON s.id=p.set_id
                    {market_join}
                    WHERE p.set_id=:set_id
                """
                params = {"set_id": canonical_set["id"], "q": f"%{q}%", "limit": limit, "offset": offset}
                scope = {
                    "type": "set",
                    "set_id": canonical_set["id"],
                    "set_code": canonical_set["code"],
                    "set_region": canonical_set["region"],
                }

            rows_sql = text(f"{common_select} {base_from} {q_clause} {price_clause} ORDER BY {order_sql} LIMIT :limit OFFSET :offset")
            count_sql = text(f"SELECT COUNT(*) {base_from} {q_clause} {price_clause}")
            unfiltered_sql = text(f"SELECT COUNT(*) {base_from}")
            rows = session.execute(rows_sql, params).mappings().all()
            total = int(session.execute(count_sql, params).scalar_one())
            unfiltered_total = int(session.execute(unfiltered_sql, params).scalar_one())
    except SQLAlchemyError:
        return jsonify({"error": "set_checklist_unavailable"}), 503

    items = []
    for source_row in rows:
        row = dict(source_row)
        if row.get("cardmarket_price") is not None:
            row["cardmarket_price"] = float(row["cardmarket_price"])
        if hasattr(row.get("cardmarket_as_of"), "isoformat"):
            row["cardmarket_as_of"] = row["cardmarket_as_of"].isoformat()
        releases = row.get("physical_releases") or []
        row["physical_release_names"] = [release.get("name") for release in releases if isinstance(release, dict) and release.get("name")]
        items.append(row)

    return jsonify({
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "unfiltered_total": unfiltered_total,
        "sort": sort,
        "has_price": has_price,
        "query": q,
        "scope": scope,
    })
