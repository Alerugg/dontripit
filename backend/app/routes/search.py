from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import db

search_bp = Blueprint("search", __name__)


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
