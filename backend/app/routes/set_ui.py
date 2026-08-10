from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db

set_ui_bp = Blueprint("set_ui", __name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


@set_ui_bp.get("/api/v1/set-ui/prints")
def list_set_ui_prints():
    """Read-only projection for public set checklists.

    It preserves exact Print identity while joining the human card name and the
    primary image needed by the web UI. Pagination stays deliberately bounded;
    the frontend BFF can walk it in small pages for complete set views.
    """
    game = str(request.args.get("game") or "").strip().lower()
    set_code = str(request.args.get("set_code") or "").strip().lower()
    if not game or not set_code:
        return jsonify({"error": "invalid_params", "detail": "game and set_code are required"}), 400

    limit = _bounded_int(request.args.get("limit"), default=50, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=1000)

    rows_sql = text(
        """
        SELECT p.id,
               p.id AS print_id,
               p.card_id,
               c.name AS card_name,
               c.name AS name,
               s.code AS set_code,
               s.name AS set_name,
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
               ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = c.game_id
        WHERE g.slug = :game
          AND lower(s.code) = :set_code
        ORDER BY p.collector_number ASC, p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    total_sql = text(
        """
        SELECT COUNT(*)
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = c.game_id
        WHERE g.slug = :game
          AND lower(s.code) = :set_code
        """
    )

    params = {"game": game, "set_code": set_code, "limit": limit, "offset": offset}
    try:
        with db.SessionLocal() as session:
            rows = session.execute(rows_sql, params).mappings().all()
            total = int(session.execute(total_sql, params).scalar_one())
    except SQLAlchemyError:
        return jsonify({"error": "set_checklist_unavailable"}), 503

    return jsonify(
        {
            "items": [dict(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
        }
    )
