from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import db
from app.search_v2.advanced import advanced_onepiece_search
from app.search_v2.facet_values import onepiece_facet_values
from app.search_v2.mtg_advanced import advanced_mtg_search
from app.search_v2.mtg_facet_values import mtg_facet_values
from app.search_v2.mtg_query import normal_mtg_search
from app.search_v2.onepiece_exact_collector import exact_onepiece_collector_search
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.pokemon_facet_values import pokemon_facet_values
from app.search_v2.pokemon_query import normal_pokemon_search
from app.search_v2.query import facet_definitions, normal_search
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_facet_values import yugioh_facet_values
from app.search_v2.yugioh_query import normal_yugioh_search


search_v2_bp = Blueprint("search_v2", __name__)
SEARCH_V2_ADVANCED_GAMES = {"onepiece", "pokemon", "yugioh", "mtg"}
MAX_QUERY_LENGTH = 200
MAX_SEARCH_LIMIT = 100
MAX_FACET_LIMIT = 100


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _query(value) -> str:
    return str(value or "").strip()[:MAX_QUERY_LENGTH]


def _normal_search_for_game(session, *, query: str, game: str | None, limit: int):
    exact_onepiece = exact_onepiece_collector_search(
        session,
        query=query,
        game=game,
        limit=limit,
    )
    if exact_onepiece is not None:
        return exact_onepiece
    if game == "pokemon":
        return normal_pokemon_search(session, query=query, limit=limit)
    if game == "yugioh":
        return normal_yugioh_search(session, query=query, limit=limit)
    if game == "mtg":
        return normal_mtg_search(session, query=query, limit=limit)
    return normal_search(session, query=query, game_slug=game, limit=limit)


def _suggestion_row(row: dict) -> dict:
    matched = row.get("matched_print") or row
    return {
        "type": row.get("type") or "card",
        "card_id": row["card_id"],
        "print_id": row.get("print_id") or matched.get("print_id"),
        "name": row["name"],
        "game": row["game"],
        "collector_number": matched.get("collector_number"),
        "set_code": matched.get("set_code"),
        "image_url": matched.get("primary_image_url"),
    }


@search_v2_bp.get("/api/v2/search")
def search_v2():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"error": "q is required"}), 400
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=MAX_SEARCH_LIMIT)

    with db.SessionLocal() as session:
        items = _normal_search_for_game(session, query=q, game=game, limit=limit)
    return jsonify({"query": q, "game": game, "items": items, "count": len(items)})


@search_v2_bp.get("/api/v2/search/suggest")
def search_v2_suggest():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"query": q, "items": []})
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = min(max(request.args.get("limit", default=8, type=int) or 8, 1), 15)

    with db.SessionLocal() as session:
        rows = _normal_search_for_game(session, query=q, game=game, limit=limit)

    items = [_suggestion_row(row) for row in rows]
    return jsonify({"query": q, "game": game, "items": items})


@search_v2_bp.get("/api/v2/games/<game_slug>/facets")
def search_v2_facets(game_slug: str):
    game_slug = game_slug.strip().lower()
    with db.SessionLocal() as session:
        facets = facet_definitions(session, game_slug=game_slug)
    if not facets:
        return jsonify({"error": "game_not_search_v2_ready", "game": game_slug}), 404

    groups: dict[str, list[dict]] = {}
    for facet in facets:
        groups.setdefault(facet.get("group") or "Other", []).append(facet)
    return jsonify({"game": game_slug, "facets": facets, "groups": groups})


@search_v2_bp.get("/api/v2/games/<game_slug>/facets/<facet_key>/values")
def search_v2_facet_values(game_slug: str, facet_key: str):
    game_slug = game_slug.strip().lower()
    facet_key = facet_key.strip().lower()
    if game_slug not in SEARCH_V2_ADVANCED_GAMES:
        return jsonify({"error": "game_not_search_v2_ready", "game": game_slug}), 422

    query = _query(request.args.get("q"))
    limit = _bounded_int(request.args.get("limit"), default=30, minimum=1, maximum=MAX_FACET_LIMIT)
    try:
        with db.SessionLocal() as session:
            if game_slug == "pokemon":
                items = pokemon_facet_values(session, key=facet_key, query=query, limit=limit)
            elif game_slug == "yugioh":
                items = yugioh_facet_values(session, key=facet_key, query=query, limit=limit)
            elif game_slug == "mtg":
                items = mtg_facet_values(session, key=facet_key, query=query, limit=limit)
            else:
                items = onepiece_facet_values(session, key=facet_key, query=query, limit=limit)
    except ValueError as exc:
        return jsonify({"error": "facet_values_unavailable", "detail": str(exc)}), 400

    return jsonify(
        {
            "game": game_slug,
            "facet": facet_key,
            "query": query,
            "items": items,
            "count": len(items),
        }
    )


@search_v2_bp.post("/api/v2/search/advanced")
def search_v2_advanced():
    body = request.get_json(silent=True) or {}
    game = str(body.get("game") or "").strip().lower()
    if not game:
        return jsonify({"error": "game is required"}), 400
    if game not in SEARCH_V2_ADVANCED_GAMES:
        return jsonify({"error": "game_not_search_v2_ready", "game": game}), 422

    filters = body.get("filters") or {}
    if not isinstance(filters, dict):
        return jsonify({"error": "filters must be an object"}), 400

    try:
        with db.SessionLocal() as session:
            if game == "pokemon":
                search_fn = advanced_pokemon_search
            elif game == "yugioh":
                search_fn = advanced_yugioh_search
            elif game == "mtg":
                search_fn = advanced_mtg_search
            else:
                search_fn = advanced_onepiece_search
            result = search_fn(
                session,
                filters=filters,
                query=_query(body.get("q")),
                limit=_bounded_int(body.get("limit"), default=50, minimum=1, maximum=MAX_SEARCH_LIMIT),
                offset=_bounded_int(body.get("offset"), default=0, minimum=0, maximum=10_000),
            )
    except ValueError as exc:
        return jsonify({"error": "invalid_filters", "detail": str(exc)}), 400

    return jsonify({"game": game, **result})
