from flask import Blueprint, jsonify, request
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from app import db
from app.models import PrintIdentifier
from app.onepiece_legacy_policy import is_onepiece_canonical_external_id

set_ui_bp = Blueprint("set_ui", __name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _canonical_onepiece_rows(session, rows) -> list[dict]:
    """Return only Bandai-identified physical One Piece prints.

    Legacy One Piece rows are intentionally kept in the database because older
    Collection/Wishlist entries may still reference their numeric Print ids. The
    public set checklist, however, must use the canonical physical Print rows so
    exact Cardmarket links, exact variants and prices resolve against the same
    identity.
    """
    row_dicts = [dict(row) for row in rows]
    print_ids = [int(row["print_id"]) for row in row_dicts if row.get("print_id") is not None]
    if not print_ids:
        return []

    identifier_rows = session.execute(
        select(PrintIdentifier.print_id, PrintIdentifier.external_id).where(
            PrintIdentifier.print_id.in_(print_ids)
        )
    ).all()
    canonical_print_ids = {
        int(print_id)
        for print_id, external_id in identifier_rows
        if is_onepiece_canonical_external_id(external_id)
    }
    return [row for row in row_dicts if int(row["print_id"]) in canonical_print_ids]


@set_ui_bp.get("/api/v1/set-ui/prints")
def list_set_ui_prints():
    """Read-only projection for public set checklists.

    It preserves exact Print identity while joining the human card name and the
    primary image needed by the web UI. Pagination stays deliberately bounded;
    the frontend BFF can walk it in small pages for complete set views.

    One Piece is special-cased at read time while its historical catalog is being
    retained for user-library compatibility: only prints carrying a canonical
    Bandai physical identifier are exposed publicly. This makes the Print ids in
    the checklist the same Print ids used by exact Cardmarket mappings.
    """
    game = str(request.args.get("game") or "").strip().lower()
    set_code = str(request.args.get("set_code") or "").strip().lower()
    if not game or not set_code:
        return jsonify({"error": "invalid_params", "detail": "game and set_code are required"}), 400

    limit = _bounded_int(request.args.get("limit"), default=50, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=1000)

    rows_base_sql = """
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
    """
    rows_sql = text(f"{rows_base_sql} LIMIT :limit OFFSET :offset")
    all_rows_sql = text(rows_base_sql)
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
            if game == "onepiece":
                all_rows = session.execute(all_rows_sql, params).mappings().all()
                canonical_rows = _canonical_onepiece_rows(session, all_rows)
                total = len(canonical_rows)
                rows = canonical_rows[offset : offset + limit]
            else:
                rows = [dict(row) for row in session.execute(rows_sql, params).mappings().all()]
                total = int(session.execute(total_sql, params).scalar_one())
    except SQLAlchemyError:
        return jsonify({"error": "set_checklist_unavailable"}), 503

    return jsonify(
        {
            "items": rows,
            "limit": limit,
            "offset": offset,
            "total": total,
        }
    )
