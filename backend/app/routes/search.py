from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
import re

from app import db

search_bp = Blueprint("search", __name__)


def _looks_like_code_query(raw_query: str) -> bool:
    stripped = raw_query.strip()
    normalized = "".join(stripped.split())
    if len(normalized) < 2 or len(normalized) > 16:
        return False

    if re.fullmatch(r"[a-zA-Z0-9]{2,8}[-_][a-zA-Z0-9]{1,8}", normalized):
        return True

    has_alpha = any(char.isalpha() for char in normalized)
    has_digit = any(char.isdigit() for char in normalized)
    if has_alpha and has_digit:
        return True

    if normalized.isalpha() and normalized.upper() == normalized and 2 <= len(normalized) <= 5:
        return True

    tokens = [token for token in re.split(r"[\s/]+", stripped) if token]
    if len(tokens) >= 2:
        structured_tokens = sum(
            1
            for token in tokens
            if re.fullmatch(r"[A-Za-z]{1,5}[0-9]{1,4}", token)
            or re.fullmatch(r"[A-Za-z0-9]{2,8}[-_][A-Za-z0-9]{1,8}", token)
        )
        if structured_tokens >= (len(tokens) / 2):
            return True

    return False


def _fallback_search_rows(session, *, like: str, game: str, result_type: str | None, limit: int, offset: int):
    fallback = text(
        """
        SELECT * FROM (
          SELECT 'card' AS type, c.id, c.name AS title, '' AS subtitle, g.slug AS game,
                 NULL AS set_code, NULL AS collector_number, NULL AS variant, NULL AS primary_image_url
          FROM cards c JOIN games g ON g.id = c.game_id WHERE lower(c.name) LIKE :like
          UNION ALL
          SELECT 'set', s.id, s.name, s.code, g.slug,
                 NULL, NULL, NULL, NULL
          FROM sets s JOIN games g ON g.id = s.game_id WHERE lower(s.name) LIKE :like OR lower(s.code) LIKE :like
          UNION ALL
          SELECT 'print', p.id, c.name, (s.code || ' #' || p.collector_number), g.slug,
                 s.code, p.collector_number, p.variant,
                 (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1)
          FROM prints p JOIN cards c ON c.id=p.card_id JOIN sets s ON s.id=p.set_id JOIN games g ON g.id=s.game_id
          WHERE lower(c.name) LIKE :like OR lower(p.collector_number) LIKE :like OR lower(s.code) LIKE :like
        ) t WHERE (:game = '' OR t.game = :game)
          AND (:type IS NULL OR t.type = :type)
        LIMIT :limit OFFSET :offset
        """
    )
    return session.execute(
        fallback,
        {"like": like, "game": game, "type": result_type, "limit": limit, "offset": offset},
    ).mappings().all()


def _fallback_suggest_rows(session, *, q: str, game: str, limit: int):
    term = " ".join(q.lower().split())
    looks_like_code = 1 if _looks_like_code_query(q) else 0
    params = {
        "q": term,
        "prefix": f"{term}%",
        "contains": f"%{term}%",
        "token_prefix": f"% {term}%",
        "looks_like_code": looks_like_code,
        "game": game,
        "limit": limit,
    }
    sql = text(
        """
        WITH card_print_counts AS (
          SELECT p.card_id, CAST(COUNT(*) AS FLOAT) AS print_count
          FROM prints p
          GROUP BY p.card_id
        ),
        scored AS (
          SELECT
            sd.doc_type AS type,
            sd.object_id AS id,
            sd.title,
            COALESCE(sd.subtitle, '') AS subtitle,
            g.slug AS game,
            s.code AS set_code,
            p.collector_number,
            p.variant,
            COALESCE(
              (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1),
              (SELECT pi2.url FROM print_images pi2 JOIN prints p2 ON p2.id = pi2.print_id WHERE p2.card_id = p.card_id AND pi2.is_primary IS TRUE ORDER BY pi2.id LIMIT 1)
            ) AS primary_image_url,
            (
              CASE WHEN lower(sd.title) = :q THEN 2400.0 ELSE 0.0 END +
              CASE WHEN lower(sd.title) LIKE :prefix THEN (CASE WHEN :looks_like_code = 1 THEN 900.0 ELSE 1900.0 END) ELSE 0.0 END +
              CASE WHEN (' ' || lower(sd.title)) LIKE :token_prefix THEN 950.0 ELSE 0.0 END +
              CASE WHEN lower(sd.title) LIKE :contains THEN (CASE WHEN :looks_like_code = 1 THEN 180.0 ELSE 460.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(p.collector_number, '')) = :q THEN (CASE WHEN :looks_like_code = 1 THEN 4200.0 ELSE 260.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(s.code, '')) = :q THEN (CASE WHEN :looks_like_code = 1 THEN 3800.0 ELSE 220.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(p.collector_number, '')) LIKE :prefix THEN (CASE WHEN :looks_like_code = 1 THEN 2800.0 ELSE 90.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(s.code, '')) LIKE :prefix THEN (CASE WHEN :looks_like_code = 1 THEN 2600.0 ELSE 70.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(p.collector_number, '')) LIKE :contains THEN (CASE WHEN :looks_like_code = 1 THEN 650.0 ELSE 20.0 END) ELSE 0.0 END +
              CASE WHEN lower(COALESCE(s.code, '')) LIKE :contains THEN (CASE WHEN :looks_like_code = 1 THEN 720.0 ELSE 25.0 END) ELSE 0.0 END +
              CASE WHEN :looks_like_code = 1 AND lower(COALESCE(s.code, '')) LIKE :prefix THEN (120.0 / (1 + abs(length(lower(COALESCE(s.code, ''))) - length(:q)))) ELSE 0.0 END +
              CASE WHEN :looks_like_code = 1 AND lower(COALESCE(p.collector_number, '')) LIKE :prefix THEN (80.0 / (1 + abs(length(lower(COALESCE(p.collector_number, ''))) - length(:q)))) ELSE 0.0 END +
              CASE WHEN :looks_like_code = 0 AND sd.doc_type = 'card' THEN 260.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 0 AND sd.doc_type = 'print' THEN -120.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 0 AND sd.doc_type = 'set' THEN -80.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 0 AND sd.doc_type = 'card' AND lower(sd.title) LIKE :prefix THEN 240.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 0 AND sd.doc_type = 'print' AND lower(sd.title) LIKE :prefix THEN 80.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 1 AND sd.doc_type = 'print' THEN 140.0 ELSE 0.0 END +
              CASE WHEN :looks_like_code = 1 THEN -35.0 * (
                ROW_NUMBER() OVER (
                  PARTITION BY lower(COALESCE(s.code, ''))
                  ORDER BY
                    CASE WHEN lower(COALESCE(p.collector_number, '')) = :q THEN 0 ELSE 1 END,
                    length(COALESCE(p.collector_number, '')) ASC,
                    lower(COALESCE(p.collector_number, '')) ASC,
                    sd.object_id ASC
                ) - 1
              ) ELSE 0.0 END +
              (CASE WHEN COALESCE(cpc.print_count, 0) > 50 THEN 50 ELSE COALESCE(cpc.print_count, 0) END) * 5.0 +
              CASE WHEN sd.doc_type = 'card' THEN (220.0 / (sd.object_id + 20.0)) ELSE 0.0 END
            ) AS score,
            ROW_NUMBER() OVER (
              PARTITION BY lower(sd.title)
              ORDER BY
                (
                  CASE WHEN lower(sd.title) = :q THEN 1 ELSE 0 END
                ) DESC,
                (
                  CASE WHEN lower(COALESCE(p.collector_number, '')) = :q THEN 1 ELSE 0 END
                ) DESC,
                (
                  CASE WHEN sd.doc_type = 'card' THEN 1 ELSE 0 END
                ) DESC,
                sd.object_id ASC
            ) AS title_rank
          FROM search_documents sd
          JOIN games g ON g.id = sd.game_id
          LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
          LEFT JOIN card_print_counts cpc ON cpc.card_id = COALESCE(p.card_id, CASE WHEN sd.doc_type = 'card' THEN sd.object_id ELSE NULL END)
          LEFT JOIN sets s ON (
            (sd.doc_type = 'print' AND s.id = p.set_id)
            OR (sd.doc_type = 'set' AND s.id = sd.object_id)
          )
          WHERE (
            lower(sd.title) LIKE :contains
            OR lower(COALESCE(p.collector_number, '')) LIKE :contains
            OR lower(COALESCE(s.code, '')) LIKE :contains
          )
          AND (:game = '' OR g.slug = :game)
        )
        SELECT *
        FROM scored
        WHERE title_rank <= 2
        ORDER BY score DESC, (CASE WHEN type = 'card' THEN 0 WHEN type = 'print' THEN 1 ELSE 2 END), game ASC, title ASC, id ASC
        LIMIT :limit
        """
    )
    return session.execute(sql, params).mappings().all()


@search_bp.get("/api/search")
@search_bp.get("/api/v1/search")
def search():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"error": "q is required (min 2 chars)"}), 400

    game = request.args.get("game", "").strip()
    result_type = (request.args.get("type") or "").strip() or None
    limit = min(max(request.args.get("limit", default=20, type=int) or 20, 1), 100)
    offset = max(request.args.get("offset", default=0, type=int) or 0, 0)

    with db.SessionLocal() as session:
        try:
            if session.bind.dialect.name == "postgresql":
                sql = text(
                    """
                    WITH query AS (SELECT plainto_tsquery('simple', :q) AS term)
                    SELECT sd.doc_type AS type, sd.object_id AS id, sd.title, sd.subtitle,
                           (
                               CASE WHEN sd.doc_type = 'card' AND lower(sd.title) = lower(:q) THEN 600.0 ELSE 0.0 END +
                               CASE WHEN sd.doc_type = 'print' AND lower(sd.title) = lower(:q) THEN 500.0 ELSE 0.0 END +
                               CASE WHEN lower(coalesce(p.collector_number, '')) = lower(:q) THEN 400.0 ELSE 0.0 END +
                               CASE WHEN lower(coalesce(s.code, '')) = lower(:q) THEN 300.0 ELSE 0.0 END +
                               CASE WHEN lower(sd.title) LIKE lower(:q) || '%' THEN 200.0 ELSE 0.0 END +
                               CASE WHEN lower(sd.title) LIKE '%' || lower(:q) || '%' THEN 100.0 ELSE 0.0 END +
                               ts_rank(
                                   to_tsvector(
                                       'simple',
                                       coalesce(sd.title, '') || ' ' || coalesce(sd.subtitle, '') || ' ' || coalesce(sd.tsv, '')
                                   ),
                                   query.term
                               )
                           ) AS score,
                           s.code AS set_code, p.collector_number, p.variant,
                           (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary = true ORDER BY pi.id LIMIT 1) AS primary_image_url
                    FROM search_documents sd
                    CROSS JOIN query
                    JOIN games g ON g.id = sd.game_id
                    LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
                    LEFT JOIN sets s ON p.set_id = s.id
                    WHERE to_tsvector(
                            'simple',
                            coalesce(sd.title, '') || ' ' || coalesce(sd.subtitle, '') || ' ' || coalesce(sd.tsv, '')
                          ) @@ query.term
                      AND (:game = '' OR g.slug = :game)
                      AND (:type IS NULL OR sd.doc_type = :type)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                )
                rows = session.execute(sql, {"q": q, "game": game, "type": result_type, "limit": limit, "offset": offset}).mappings().all()
            else:
                like = f"%{q.lower()}%"
                sql = text(
                    """
                    SELECT sd.doc_type AS type, sd.object_id AS id, sd.title, coalesce(sd.subtitle, '') AS subtitle,
                           (
                               CASE WHEN sd.doc_type = 'card' AND lower(sd.title) = lower(:q) THEN 600.0 ELSE 0.0 END +
                               CASE WHEN sd.doc_type = 'print' AND lower(sd.title) = lower(:q) THEN 500.0 ELSE 0.0 END +
                               CASE WHEN lower(coalesce(p.collector_number, '')) = lower(:q) THEN 400.0 ELSE 0.0 END +
                               CASE WHEN lower(coalesce(s.code, '')) = lower(:q) THEN 300.0 ELSE 0.0 END +
                               CASE WHEN lower(sd.title) LIKE lower(:q) || '%' THEN 200.0 ELSE 0.0 END +
                               CASE WHEN lower(sd.title) LIKE '%' || lower(:q) || '%' THEN 100.0 ELSE 0.0 END +
                               1.0
                           ) AS score,
                           s.code AS set_code, p.collector_number, p.variant,
                           (SELECT pi.url FROM print_images pi WHERE pi.print_id = p.id AND pi.is_primary IS TRUE ORDER BY pi.id LIMIT 1) AS primary_image_url
                    FROM search_documents sd
                    JOIN games g ON g.id = sd.game_id
                    LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
                    LEFT JOIN sets s ON p.set_id = s.id
                    WHERE lower(coalesce(sd.tsv, sd.title || ' ' || coalesce(sd.subtitle, ''))) LIKE :like
                      AND (:game = '' OR g.slug = :game)
                      AND (:type IS NULL OR sd.doc_type = :type)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                )
                rows = session.execute(sql, {"q": q, "like": like, "game": game, "type": result_type, "limit": limit, "offset": offset}).mappings().all()
        except ProgrammingError:
            session.rollback()
            like = f"%{q.lower()}%"
            rows = _fallback_search_rows(session, like=like, game=game, result_type=result_type, limit=limit, offset=offset)

        if not rows:
            like = f"%{q.lower()}%"
            rows = _fallback_search_rows(session, like=like, game=game, result_type=result_type, limit=limit, offset=offset)

    return jsonify([dict(row) for row in rows])


@search_bp.get("/api/search/suggest")
@search_bp.get("/api/v1/search/suggest")
def suggest():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify([])

    game = request.args.get("game", "").strip()
    limit = min(max(request.args.get("limit", default=10, type=int) or 10, 1), 10)

    with db.SessionLocal() as session:
        try:
            rows = _fallback_suggest_rows(session, q=q, game=game, limit=limit)
        except ProgrammingError:
            session.rollback()
            rows = _fallback_suggest_rows(session, q=q, game=game, limit=limit)

    return jsonify([dict(row) for row in rows])
