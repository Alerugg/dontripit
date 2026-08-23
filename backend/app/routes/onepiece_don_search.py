from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app import db
from app.search_v2.onepiece_don_query import onepiece_don_market_page


onepiece_don_search_bp = Blueprint("onepiece_don_search", __name__)
MAX_QUERY_LENGTH = 200
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_OFFSET = 100_000


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _access_limit(maximum: int) -> int:
    plan = (getattr(g, "api_meta", None) or {}).get("plan")
    return min(maximum, 50) if plan == "public" else maximum


def _query(value) -> str:
    return str(value or "").strip()[:MAX_QUERY_LENGTH]


@onepiece_don_search_bp.get("/api/v2/search/don")
def onepiece_don_search():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"error": "q is required"}), 400
    game = str(request.args.get("game") or "onepiece").strip().lower()
    if game != "onepiece":
        return jsonify({"error": "don_search_only_supports_onepiece", "game": game}), 422

    limit = _bounded_int(
        request.args.get("limit"),
        default=24,
        minimum=1,
        maximum=_access_limit(MAX_SEARCH_LIMIT),
    )
    offset = _bounded_int(
        request.args.get("offset"),
        default=0,
        minimum=0,
        maximum=MAX_SEARCH_OFFSET,
    )
    with db.SessionLocal() as session:
        page = onepiece_don_market_page(
            session,
            query=q,
            limit=limit,
            offset=offset,
        )
    return jsonify(
        {
            "query": q,
            "game": "onepiece",
            "don_only": True,
            "items": page["items"],
            "count": len(page["items"]),
            "total": page["total"],
            "total_prints": None,
            "limit": page["limit"],
            "offset": page["offset"],
            "has_more": page["has_more"],
            "next_offset": page["next_offset"],
            "pagination_mode": "onepiece_don_source_owned",
            "identity_scope": page["identity_scope"],
        }
    )


@onepiece_don_search_bp.get("/api/v2/search/don/suggest")
def onepiece_don_search_suggest():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"query": q, "game": "onepiece", "don_only": True, "items": []})
    limit = _bounded_int(request.args.get("limit"), default=8, minimum=1, maximum=15)
    with db.SessionLocal() as session:
        page = onepiece_don_market_page(session, query=q, limit=limit, offset=0)
    items = [
        {
            "type": item["type"],
            "identity_scope": item["identity_scope"],
            "card_id": None,
            "print_id": None,
            "name": item["name"],
            "subject": item["subject"],
            "game": "onepiece",
            "collector_number": None,
            "set_code": None,
            "image_url": item["primary_image_url"],
            "cardmarket_id_product": item["cardmarket_id_product"],
            "cardmarket_price": item["cardmarket_price"],
            "cardmarket_currency": item["cardmarket_currency"],
        }
        for item in page["items"]
    ]
    return jsonify(
        {
            "query": q,
            "game": "onepiece",
            "don_only": True,
            "identity_scope": "source_owned",
            "items": items,
        }
    )
