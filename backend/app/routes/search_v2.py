from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from app import db
from app.search_v2.advanced import advanced_onepiece_search
from app.search_v2.exhaustive_name_query import exhaustive_name_page
from app.search_v2.facet_values import onepiece_facet_values
from app.search_v2.mtg_advanced import advanced_mtg_search
from app.search_v2.mtg_facet_values import mtg_facet_values
from app.search_v2.mtg_query import normal_mtg_search
from app.search_v2.normalization import normalize_language
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
YUGIOH_DISPLAY_LANGUAGES = {"en", "es", "ja"}
MAX_QUERY_LENGTH = 200
MAX_SEARCH_LIMIT = 100
MAX_SEARCH_OFFSET = 100_000
MAX_FACET_LIMIT = 100


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


def _query(value) -> str:
    return str(value or "").strip()[:MAX_QUERY_LENGTH]


def _access_limit(maximum: int) -> int:
    plan = (getattr(g, "api_meta", None) or {}).get("plan")
    return min(maximum, 50) if plan == "public" else maximum


def _yugioh_display_language(value) -> str | None:
    if value is None:
        return None
    raw_values = list(value) if isinstance(value, (list, tuple, set)) else [value]
    tokens: list[str] = []
    for raw_value in raw_values:
        tokens.extend(part.strip() for part in str(raw_value or "").split(","))

    normalized_values: list[str] = []
    for raw in tokens:
        if not raw:
            continue
        if raw.lower() == "all":
            return None
        normalized = normalize_language(raw)
        if normalized not in YUGIOH_DISPLAY_LANGUAGES:
            raise ValueError(
                "Yu-Gi-Oh language must be all or a comma-separated selection of: en, es, ja"
            )
        if normalized not in normalized_values:
            normalized_values.append(normalized)
    return ",".join(normalized_values) or None


def _normal_search_for_game(session, *, query: str, game: str | None, limit: int, language: str | None = None):
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
        return normal_yugioh_search(session, query=query, limit=limit, language=language)
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
        "display_language": row.get("display_language") or matched.get("display_language"),
        "available_languages": row.get("available_languages") or matched.get("available_languages") or [],
    }


@search_v2_bp.get("/api/v2/search")
def search_v2():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"error": "q is required"}), 400
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=_access_limit(MAX_SEARCH_LIMIT))
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=MAX_SEARCH_OFFSET)
    try:
        language = _yugioh_display_language(request.args.get("language")) if game == "yugioh" else None
    except ValueError as exc:
        return jsonify({"error": "invalid_language", "detail": str(exc)}), 400

    with db.SessionLocal() as session:
        # Canonical-name matches are intentionally strict and exhaustive. This
        # prevents fuzzy candidates or a top-N cap from displacing real cards
        # for common names such as Pikachu or Luffy. Yu-Gi-Oh language-scoped
        # searches keep their localization-aware specialized path.
        page = None
        if language is None:
            page = exhaustive_name_page(
                session,
                query=q,
                game=game,
                limit=limit,
                offset=offset,
            )

        if page and page["total"] > 0:
            return jsonify(
                {
                    "query": q,
                    "game": game,
                    "language": language or "all",
                    "items": page["items"],
                    "count": len(page["items"]),
                    "total": page["total"],
                    "total_prints": page["total_prints"],
                    "limit": page["limit"],
                    "offset": page["offset"],
                    "has_more": page["has_more"],
                    "next_offset": page["next_offset"],
                    "pagination_mode": "canonical_name",
                }
            )

        # Collector/structured/fuzzy searches preserve the existing ranking
        # engine. Fetch one extra row so shallow pagination remains stable while
        # advanced search remains the exhaustive path for structured filters.
        fetch_limit = min(MAX_SEARCH_LIMIT, offset + limit + 1)
        ranked = _normal_search_for_game(
            session,
            query=q,
            game=game,
            limit=fetch_limit,
            language=language,
        )
        items = ranked[offset : offset + limit]
        has_more = len(ranked) > offset + len(items)
        next_offset = offset + len(items) if has_more else None
        total = len(ranked) if not has_more else None

    return jsonify(
        {
            "query": q,
            "game": game,
            "language": language or "all",
            "items": items,
            "count": len(items),
            "total": total,
            "total_prints": None,
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "next_offset": next_offset,
            "pagination_mode": "ranked_fallback",
        }
    )


@search_v2_bp.get("/api/v2/search/suggest")
def search_v2_suggest():
    q = _query(request.args.get("q"))
    if not q:
        return jsonify({"query": q, "items": []})
    game = str(request.args.get("game") or "").strip().lower() or None
    limit = min(max(request.args.get("limit", default=8, type=int) or 8, 1), 15)
    try:
        language = _yugioh_display_language(request.args.get("language")) if game == "yugioh" else None
    except ValueError as exc:
        return jsonify({"error": "invalid_language", "detail": str(exc)}), 400

    with db.SessionLocal() as session:
        rows = _normal_search_for_game(session, query=q, game=game, limit=limit, language=language)

    items = [_suggestion_row(row) for row in rows]
    return jsonify({"query": q, "game": game, "language": language or "all", "items": items})


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
    limit = _bounded_int(request.args.get("limit"), default=30, minimum=1, maximum=_access_limit(MAX_FACET_LIMIT))
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
        language = _yugioh_display_language(body.get("language")) if game == "yugioh" else None
        with db.SessionLocal() as session:
            if game == "pokemon":
                search_fn = advanced_pokemon_search
            elif game == "yugioh":
                search_fn = advanced_yugioh_search
            elif game == "mtg":
                search_fn = advanced_mtg_search
            else:
                search_fn = advanced_onepiece_search
            search_kwargs = {
                "filters": filters,
                "query": _query(body.get("q")),
                "sort": str(body.get("sort") or "relevance"),
                "has_price": _bool(body.get("has_price")),
                "limit": _bounded_int(
                    body.get("limit"),
                    default=50,
                    minimum=1,
                    maximum=_access_limit(MAX_SEARCH_LIMIT),
                ),
                "offset": _bounded_int(
                    body.get("offset"),
                    default=0,
                    minimum=0,
                    maximum=MAX_SEARCH_OFFSET,
                ),
            }
            if game == "yugioh":
                search_kwargs["language"] = language
            result = search_fn(session, **search_kwargs)
    except ValueError as exc:
        return jsonify({"error": "invalid_filters", "detail": str(exc)}), 400

    return jsonify({"game": game, "language": language or "all", **result})
