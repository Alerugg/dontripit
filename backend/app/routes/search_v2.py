from __future__ import annotations

from flask import Blueprint, jsonify, request

from app import db
from app.search_v2.advanced import advanced_onepiece_search
from app.search_v2.facet_values import onepiece_facet_values
from app.search_v2.mtg_advanced import advanced_mtg_search
from app.search_v2.mtg_facet_values import mtg_facet_values
from app.search_v2.pokemon_advanced import advanced_pokemon_search
from app.search_v2.pokemon_facet_values import pokemon_facet_values
from app.search_v2.pokemon_query import normal_pokemon_search
from app.search_v2.query import facet_definitions, normal_search
from app.search_v2.yugioh_advanced import advanced_yugioh_search
from app.search_v2.yugioh_facet_values import yugioh_facet_values
from app.search_v2.yugioh_query import normal_yugioh_search


search_v2_bp = Blueprint("search_v2", __name__)
SEARCH_V2_ADVANCED_GAMES = {"onepiece", "pokemon", "yugioh", "mtg"}


def _normal_search_for_game(session, *, query: str, game: str | None, limit: int):
    if game == "pokemon":
        return normal_pokemon_search(session, query=query, limit=limit)
    if game == "yugioh":
        return normal_yugioh_search(session, query=query, limit=limit)
    # MTG intentionally starts on the proven generic logical-Card search path.
    # A game-specific natural query is only justified if the disposable shadow
    # benchmark demonstrates a quality/latency gap worth extra code/index cost.
    return normal_search(session, query=query, game_slug=game, limit=limit)


@search_v2_bp.get("/api/v2/search")
def search_v2():
    q = str(request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "q is required"}), 400
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = request.args.get("limit", default=24, type=int) or 24

    with db.SessionLocal() as session:
        items = _normal_search_for_game(session, query=q, game=game, limit=limit)
    return jsonify({"query": q, "game": game, "items": items, "count": len(items)})


@search_v2_bp.get("/api/v2/search/suggest")
def search_v2_suggest():
    q = str(request.args.get("q") or "").strip()
    if not q:
        return jsonify({"query": q, "items": []})
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = min(max(request.args.get("limit", default=8, type=int) or 8, 1), 15)

    with db.SessionLocal() as session:
        cards = _normal_search_for_game(session, query=q, game=game, limit=limit)

    items = [
        {
            "type": "card",
            "card_id": row["card_id"],
            "name": row["name"],
            "game": row["game"],
            "collector_number": (row.get("matched_print") or {}).get("collector_number"),
            "set_code": (row.get("matched_print") or {}).get("set_code"),
            "image_url": (row.get("matched_print") or {}).get("primary_image_url"),
        }
        for row in cards
    ]
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

    query = str(request.args.get("q") or "").strip()
    limit = request.args.get("limit", default=30, type=int) or 30
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
                query=body.get("q"),
                limit=body.get("limit", 50),
                offset=body.get("offset", 0),
            )
    except ValueError as exc:
        return jsonify({"error": "invalid_filters", "detail": str(exc)}), 400

    return jsonify({"game": game, **result})