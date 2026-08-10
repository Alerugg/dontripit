import re

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


def _normalized_release_code(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _release_codes(row: dict) -> set[str]:
    """Return deterministic public aliases declared by a release row.

    ``CatalogRelease.code`` is preferred when a source provides one. Bandai's
    official One Piece card-list currently exposes the human code inside square
    brackets in the release name (for example ``[OP-16]``), so bracketed codes
    are accepted as explicit source-declared aliases too. We deliberately do
    not fuzzy-match arbitrary words from release names.
    """

    candidates = [row.get("code")]
    candidates.extend(re.findall(r"\[([^\]]+)\]", str(row.get("name") or "")))
    return {value for raw in candidates if (value := _normalized_release_code(raw))}


def _resolve_catalog_release(session, *, game: str, requested_code: str) -> dict | None:
    compact = _normalized_release_code(requested_code)
    if not compact:
        return None

    rows = session.execute(
        text(
            """
            SELECT cr.id, cr.source, cr.external_id, cr.name, cr.code,
                   cr.release_type, cr.release_date, cr.language, cr.region
            FROM catalog_releases cr
            JOIN games g ON g.id = cr.game_id
            WHERE g.slug = :game
            ORDER BY cr.id ASC
            """
        ),
        {"game": game},
    ).mappings().all()

    matches = [dict(row) for row in rows if compact in _release_codes(dict(row))]
    if not matches:
        return None

    # Prefer an official publisher source when multiple independent provenance
    # layers deliberately describe the same public release code. Within the same
    # trust tier we only resolve automatically when the alias is unambiguous.
    def trust(row: dict) -> tuple[int, int]:
        source = str(row.get("source") or "").lower()
        explicit_code = int(_normalized_release_code(row.get("code")) == compact)
        official = int("official" in source)
        return official, explicit_code

    best = max(trust(row) for row in matches)
    finalists = [row for row in matches if trust(row) == best]
    if len(finalists) != 1:
        return {"ambiguous": True, "matches": finalists}
    return finalists[0]


@set_ui_bp.get("/api/v1/set-ui/prints")
def list_set_ui_prints():
    """Read-only projection for public release/set checklists.

    Public card-game sites often organize cards by a commercial release/card
    list rather than only by collector-number family. ``CatalogRelease`` and
    ``PrintRelease`` preserve that source-defined structure (including reprints
    and mixed releases), while ``Set`` remains the canonical collector-number
    family. This endpoint therefore resolves a declared release code first and
    falls back to Set for games/releases that do not have that layer yet.

    Exact Print identity is preserved in both paths. Pagination stays bounded;
    the frontend BFF can walk it in small pages for complete checklist views.
    """

    game = str(request.args.get("game") or "").strip().lower()
    set_code = str(request.args.get("set_code") or "").strip()
    if not game or not set_code:
        return jsonify({"error": "invalid_params", "detail": "game and set_code are required"}), 400

    limit = _bounded_int(request.args.get("limit"), default=50, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=1000)

    release_rows_sql = text(
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
        FROM print_releases pr
        JOIN prints p ON p.id = pr.print_id
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        WHERE pr.release_id = :release_id
        ORDER BY p.collector_number ASC, p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    release_total_sql = text("SELECT COUNT(*) FROM print_releases WHERE release_id = :release_id")

    set_rows_sql = text(
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
    set_total_sql = text(
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

    try:
        with db.SessionLocal() as session:
            release = _resolve_catalog_release(session, game=game, requested_code=set_code)
            if release and release.get("ambiguous"):
                return (
                    jsonify(
                        {
                            "error": "release_code_ambiguous",
                            "game": game,
                            "set_code": set_code,
                            "matches": [
                                {
                                    "id": row["id"],
                                    "source": row["source"],
                                    "external_id": row["external_id"],
                                    "name": row["name"],
                                    "code": row["code"],
                                }
                                for row in release["matches"]
                            ],
                        }
                    ),
                    409,
                )

            if release:
                params = {"release_id": release["id"], "limit": limit, "offset": offset}
                rows = session.execute(release_rows_sql, params).mappings().all()
                total = int(session.execute(release_total_sql, params).scalar_one())
                scope = {
                    "type": "release",
                    "release_id": release["id"],
                    "release_source": release["source"],
                    "release_external_id": release["external_id"],
                    "release_name": release["name"],
                    "release_code": release["code"],
                }
            else:
                params = {
                    "game": game,
                    "set_code": set_code.lower(),
                    "limit": limit,
                    "offset": offset,
                }
                rows = session.execute(set_rows_sql, params).mappings().all()
                total = int(session.execute(set_total_sql, params).scalar_one())
                scope = {"type": "set", "set_code": set_code}
    except SQLAlchemyError:
        return jsonify({"error": "set_checklist_unavailable"}), 503

    return jsonify(
        {
            "items": [dict(row) for row in rows],
            "limit": limit,
            "offset": offset,
            "total": total,
            "scope": scope,
        }
    )