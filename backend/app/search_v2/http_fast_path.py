from __future__ import annotations

import re
import threading
import time
from collections import OrderedDict
from typing import Any

from flask import Flask, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.search_v2.normalization import normalize_search_text


_SEARCH_PATHS = {"/api/search", "/api/v1/search"}
_SUGGEST_PATHS = {"/api/search/suggest", "/api/v1/search/suggest"}
_SIMPLE_NAME_RE = re.compile(r"^[a-z][a-z\s'\-.]{0,31}$")
_CACHE_TTL_SECONDS = 20.0
_CACHE_MAX_ENTRIES = 256
_CACHE: OrderedDict[tuple[Any, ...], tuple[float, list[dict[str, Any]]]] = OrderedDict()
_CACHE_LOCK = threading.Lock()


def _is_interactive_name_query(raw_query: str) -> bool:
    normalized = " ".join(str(raw_query or "").strip().lower().split())
    return 1 <= len(normalized) <= 32 and _SIMPLE_NAME_RE.fullmatch(normalized) is not None


def _cache_get(key: tuple[Any, ...]) -> list[dict[str, Any]] | None:
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is None:
            return None
        created_at, rows = cached
        if now - created_at > _CACHE_TTL_SECONDS:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key)
        return [dict(row) for row in rows]


def _cache_put(key: tuple[Any, ...], rows: list[dict[str, Any]]) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), [dict(row) for row in rows])
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)


def _fast_card_rows(
    session,
    *,
    query: str,
    game: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    """Return card-first interactive results from the Search V2 projection.

    The legacy public search builds and ranks the complete ``search_documents``
    union, joins prints/sets/images, and only then applies LIMIT. That is useful
    for mixed free-form search, but it is unnecessarily expensive for the common
    UI interaction: a user typing a card name inside a selected game.

    This path starts from ``card_search_profiles`` (trigram/pattern indexed),
    pages candidate card IDs first, and enriches only the requested page. The
    original route remains the fallback for code queries, explicit print/set
    searches, unsupported text, stale/missing profiles, and non-PostgreSQL test
    databases.
    """
    q_norm = normalize_search_text(query)
    if not q_norm:
        return []

    # 1-2 character contains queries are both noisy and poor fits for trigram.
    # Prefix-only lookup keeps keystroke suggestions bounded and lets PostgreSQL
    # use the (game_id, normalized_name text_pattern_ops) index from revision 39.
    prefix_only = len(q_norm) < 3
    match_predicate = "csp.normalized_name LIKE :prefix" if prefix_only else "csp.normalized_name LIKE :contains"

    sql = text(
        f"""
        WITH candidates AS MATERIALIZED (
          SELECT
            csp.card_id,
            csp.normalized_name,
            CASE
              WHEN csp.normalized_name = :q_norm THEN 0
              -- One Piece users commonly search the character's familiar name
              -- ("Luffy", "Zoro") while canonical names are "Monkey D Luffy"
              -- and "Roronoa Zoro". Preserve the legacy relevance contract by
              -- ranking a terminal whole-name token ahead of incidental prefixes
              -- such as "Luffy & Ace" or "Zoro-Juurou".
              WHEN :game = 'onepiece' AND csp.normalized_name LIKE :suffix THEN 1
              WHEN csp.normalized_name LIKE :prefix THEN 2
              WHEN (' ' || csp.normalized_name || ' ') LIKE :word_match THEN 3
              ELSE 4
            END AS rank_bucket
          FROM card_search_profiles csp
          WHERE csp.game_id = (
              SELECT g0.id
              FROM games g0
              WHERE g0.slug = :game
              LIMIT 1
            )
            AND {match_predicate}
          ORDER BY
            rank_bucket ASC,
            length(csp.normalized_name) ASC,
            csp.normalized_name ASC,
            csp.card_id ASC
          LIMIT :limit OFFSET :offset
        )
        SELECT
          'card'::text AS type,
          c.id AS id,
          c.id AS card_id,
          c.name AS title,
          ''::text AS subtitle,
          g.slug AS game,
          NULL::text AS set_code,
          NULL::text AS set_name,
          NULL::text AS collector_number,
          NULL::text AS language,
          NULL::text AS variant,
          COALESCE(stats.variant_count, 0.0) AS variant_count,
          image.primary_image_url
        FROM candidates cand
        JOIN cards c ON c.id = cand.card_id
        JOIN games g ON g.id = c.game_id
        LEFT JOIN LATERAL (
          SELECT CAST(COUNT(*) AS FLOAT) AS variant_count
          FROM prints p_count
          WHERE p_count.card_id = c.id
        ) stats ON TRUE
        LEFT JOIN LATERAL (
          SELECT pi.url AS primary_image_url
          FROM prints p_img
          JOIN print_images pi ON pi.print_id = p_img.id
          WHERE p_img.card_id = c.id
          ORDER BY
            CASE
              WHEN lower(pi.url) LIKE '%en.onepiece-cardgame.com%' THEN 0
              WHEN lower(pi.url) LIKE '%example.cdn.onepiece%' THEN 2
              ELSE 1
            END,
            CASE WHEN pi.is_primary IS TRUE THEN 0 ELSE 1 END,
            pi.id ASC
          LIMIT 1
        ) image ON TRUE
        ORDER BY
          cand.rank_bucket ASC,
          length(cand.normalized_name) ASC,
          cand.normalized_name ASC,
          cand.card_id ASC
        """
    )
    params = {
        "q_norm": q_norm,
        "prefix": f"{q_norm}%",
        "suffix": f"% {q_norm}",
        "contains": f"%{q_norm}%",
        "word_match": f"% {q_norm} %",
        "game": game,
        "limit": limit,
        "offset": offset,
    }
    return [dict(row) for row in session.execute(sql, params).mappings().all()]


def install_search_http_fast_path(flask_app: Flask) -> None:
    """Install the interactive hot path after API auth/rate-limit middleware."""

    @flask_app.before_request
    def interactive_search_fast_path():
        if request.method != "GET":
            return None

        path = request.path
        is_search = path in _SEARCH_PATHS
        is_suggest = path in _SUGGEST_PATHS
        if not is_search and not is_suggest:
            return None

        q = request.args.get("q", "").strip()
        game = request.args.get("game", "").strip().lower()
        if not q or not game or not _is_interactive_name_query(q):
            return None

        # Explicit print/set intent must preserve the mixed legacy semantics.
        result_type = (request.args.get("type") or "").strip().lower() or None
        if is_search and result_type not in (None, "card"):
            return None

        if is_suggest:
            max_limit = 6 if len(q) == 1 else 10
            limit = min(max(request.args.get("limit", default=10, type=int) or 10, 1), max_limit)
            offset = 0
        else:
            query_length = len(normalize_search_text(q))
            default_limit = 8 if query_length == 1 else 14 if query_length == 2 else 24
            max_limit = 12 if query_length == 1 else 24 if query_length == 2 else 100
            limit = min(max(request.args.get("limit", default=default_limit, type=int) or default_limit, 1), max_limit)
            offset = max(request.args.get("offset", default=0, type=int) or 0, 0)

        cache_key = (path, normalize_search_text(q), game, result_type, limit, offset)
        cached = _cache_get(cache_key)
        if cached is not None:
            response = jsonify(cached)
            response.headers["X-Search-Path"] = "card-profile-fast"
            response.headers["X-Search-Cache"] = "HIT"
            return response

        try:
            with db.SessionLocal() as session:
                if session.bind.dialect.name != "postgresql":
                    return None
                rows = _fast_card_rows(
                    session,
                    query=q,
                    game=game,
                    limit=limit,
                    offset=offset,
                )
        except SQLAlchemyError:
            # Fail open to the existing, battle-tested public search. Performance
            # work must never turn a catalog lookup into a 500.
            return None

        if not rows:
            # Missing/stale Search V2 projection: preserve legacy correctness.
            return None

        _cache_put(cache_key, rows)
        response = jsonify(rows)
        response.headers["X-Search-Path"] = "card-profile-fast"
        response.headers["X-Search-Cache"] = "MISS"
        return response
