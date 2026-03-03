from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from app import db

search_bp = Blueprint("search", __name__)


def _is_missing_projection_error(error: ProgrammingError) -> bool:
    original = getattr(error, "orig", None)
    pgcode = getattr(original, "pgcode", None)
    if pgcode == "42P01":
        return True
    message = str(original or error).lower()
    return "print_search_projection" in message and "does not exist" in message


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

    try:
        with db.SessionLocal() as session:
            dialect = session.bind.dialect.name
            if dialect == "postgresql":
                sql_with_projection = text(
                    """
                    WITH query AS (SELECT plainto_tsquery('simple', :q) AS term)
                    SELECT sd.doc_type AS type,
                           sd.object_id AS id,
                           sd.title,
                           sd.subtitle,
                           ts_rank(sd.tsv, query.term) AS score,
                           pr.set_code,
                           pr.collector_number,
                           pr.primary_image_url
                    FROM search_documents sd
                    CROSS JOIN query
                    LEFT JOIN print_search_projection pr
                        ON sd.doc_type = 'print' AND pr.print_id = sd.object_id
                    JOIN games g ON g.id = sd.game_id
                    WHERE sd.tsv @@ query.term
                      AND (:game = '' OR g.slug = :game)
                      AND (:type IS NULL OR sd.doc_type = :type)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                )
                params = {"q": q, "game": game, "type": result_type, "limit": limit, "offset": offset}
                try:
                    rows = session.execute(sql_with_projection, params).mappings().all()
                except ProgrammingError as error:
                    if not _is_missing_projection_error(error):
                        raise
                    session.rollback()
                    sql_without_projection = text(
                        """
                        WITH query AS (SELECT plainto_tsquery('simple', :q) AS term)
                        SELECT sd.doc_type AS type,
                               sd.object_id AS id,
                               sd.title,
                               sd.subtitle,
                               ts_rank(sd.tsv, query.term) AS score
                        FROM search_documents sd
                        CROSS JOIN query
                        JOIN games g ON g.id = sd.game_id
                        WHERE sd.tsv @@ query.term
                          AND (:game = '' OR g.slug = :game)
                          AND (:type IS NULL OR sd.doc_type = :type)
                        ORDER BY score DESC, sd.title ASC
                        LIMIT :limit OFFSET :offset
                        """
                    )
                    rows = session.execute(sql_without_projection, params).mappings().all()
            else:
                like = f"%{q.lower()}%"
                sql = text(
                    """
                SELECT * FROM (
                    SELECT 'card' AS type, c.id, c.name AS title, '' AS subtitle, 1.0 AS score,
                           NULL AS set_code, NULL AS collector_number, NULL AS primary_image_url,
                           g.slug AS game
                    FROM cards c
                    JOIN games g ON g.id = c.game_id
                    WHERE LOWER(c.name) LIKE :like
                    UNION ALL
                    SELECT 'set' AS type, s.id, s.name AS title, s.code AS subtitle, 1.0 AS score,
                           NULL, NULL, NULL, g.slug AS game
                    FROM sets s
                    JOIN games g ON g.id = s.game_id
                    WHERE LOWER(s.name) LIKE :like OR LOWER(s.code) LIKE :like
                    UNION ALL
                    SELECT 'print' AS type, p.id,
                           c.name || ' #' || p.collector_number AS title,
                           s.code AS subtitle,
                           1.0 AS score,
                           s.code AS set_code,
                           p.collector_number,
                           (
                               SELECT pi.url
                               FROM print_images pi
                               WHERE pi.print_id = p.id AND pi.is_primary = 1
                               LIMIT 1
                           ) AS primary_image_url,
                           g.slug AS game
                    FROM prints p
                    JOIN cards c ON c.id = p.card_id
                    JOIN sets s ON s.id = p.set_id
                    JOIN games g ON g.id = s.game_id
                    WHERE LOWER(c.name) LIKE :like OR LOWER(s.name) LIKE :like OR LOWER(p.collector_number) LIKE :like
                ) t
                WHERE (:game = '' OR t.game = :game)
                  AND (:type IS NULL OR t.type = :type)
                ORDER BY score DESC, title ASC
                LIMIT :limit OFFSET :offset
                """
                )
                rows = session.execute(
                    sql,
                    {"like": like, "game": game, "type": result_type, "limit": limit, "offset": offset},
                ).mappings().all()
    except Exception as error:
        return jsonify({"error": "search_failed", "detail": str(error)}), 500

    return jsonify([dict(row) for row in rows])
