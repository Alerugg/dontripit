from __future__ import annotations

from flask import Blueprint, jsonify, request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app import db


card_prints_bp = Blueprint("card_prints", __name__)


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def _release_payload(row: dict) -> dict:
    release = {
        "id": int(row["id"]),
        "source": row["source"],
        "external_id": row["external_id"],
        "name": row["name"],
        "code": row["code"],
        "release_type": row["release_type"],
        "release_date": row["release_date"],
        "language": row["language"],
        "region": row["region"],
    }
    if hasattr(release.get("release_date"), "isoformat"):
        release["release_date"] = release["release_date"].isoformat()
    return release


@card_prints_bp.get("/api/v1/cards/<int:card_id>/prints")
def list_card_prints(card_id: int):
    """Paginate every exact physical Print for one logical Card.

    This endpoint exists so high-reprint cards (basic lands, Sol Ring,
    Blue-Eyes, etc.) are never silently truncated by the card detail reader.
    Each row keeps the stable Print id and its independent physical releases.
    """
    limit = _bounded_int(request.args.get("limit"), default=24, minimum=1, maximum=50)
    offset = _bounded_int(request.args.get("offset"), default=0, minimum=0, maximum=100_000)

    card_sql = text(
        """
        SELECT c.id, c.name, g.slug AS game
        FROM cards c
        JOIN games g ON g.id=c.game_id
        WHERE c.id=:card_id
        """
    )
    count_sql = text("SELECT COUNT(*) FROM prints WHERE card_id=:card_id")
    rows_sql = text(
        """
        SELECT p.id,
               p.id AS print_id,
               p.card_id,
               c.name AS card_name,
               g.slug AS game,
               s.id AS set_id,
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
                 WHERE pi.print_id=p.id
                 ORDER BY pi.is_primary DESC,pi.id ASC
                 LIMIT 1
               ) AS primary_image_url
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN games g ON g.id=c.game_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.card_id=:card_id
        ORDER BY lower(COALESCE(s.code,'')) ASC,
                 COALESCE(NULLIF(substring(COALESCE(p.collector_number,'') from '([0-9]+)[^0-9]*$'), '')::bigint, 9223372036854775807) ASC,
                 lower(COALESCE(p.collector_number,'')) ASC,
                 CASE WHEN lower(COALESCE(p.variant,'')) IN ('default','base','') THEN 0 ELSE 1 END ASC,
                 lower(COALESCE(p.variant,'')) ASC,
                 p.id ASC
        LIMIT :limit OFFSET :offset
        """
    )
    releases_sql = text(
        """
        SELECT pr.print_id,
               cr.id,cr.source,cr.external_id,cr.name,cr.code,
               cr.release_type,cr.release_date,cr.language,cr.region
        FROM print_releases pr
        JOIN catalog_releases cr ON cr.id=pr.release_id
        WHERE pr.print_id = ANY(:print_ids)
        ORDER BY pr.print_id,cr.release_date NULLS LAST,cr.id
        """
    )

    try:
        with db.SessionLocal() as session:
            card = session.execute(card_sql, {"card_id": card_id}).mappings().first()
            if card is None:
                return jsonify({"error": "not_found", "detail": f"card {card_id} not found"}), 404
            total = int(session.execute(count_sql, {"card_id": card_id}).scalar_one())
            rows = [dict(row) for row in session.execute(rows_sql, {"card_id": card_id, "limit": limit, "offset": offset}).mappings().all()]
            ids = [int(row["print_id"]) for row in rows]
            release_rows = session.execute(releases_sql, {"print_ids": ids or [-1]}).mappings().all()
    except SQLAlchemyError:
        return jsonify({"error": "card_prints_unavailable"}), 503

    releases_by_print: dict[int, list[dict]] = {}
    for source_row in release_rows:
        row = dict(source_row)
        releases_by_print.setdefault(int(row["print_id"]), []).append(_release_payload(row))

    items = []
    for row in rows:
        print_id = int(row["print_id"])
        row["physical_releases"] = releases_by_print.get(print_id, [])
        row["physical_release_names"] = [release["name"] for release in row["physical_releases"] if release.get("name")]
        items.append(row)

    return jsonify({
        "card": {"id": int(card["id"]), "name": card["name"], "game": card["game"]},
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
        "complete": offset + len(items) >= total,
    })


@card_prints_bp.get("/api/v1/prints/<int:print_id>/physical-releases")
def list_print_physical_releases(print_id: int):
    identity_sql = text(
        """
        SELECT p.id AS print_id,p.card_id,c.name AS card_name,g.slug AS game,
               s.id AS set_id,s.code AS set_code,s.name AS set_name,
               p.collector_number,p.language,p.rarity,p.is_foil,p.variant
        FROM prints p
        JOIN cards c ON c.id=p.card_id
        JOIN games g ON g.id=c.game_id
        JOIN sets s ON s.id=p.set_id
        WHERE p.id=:print_id
        """
    )
    releases_sql = text(
        """
        SELECT cr.id,cr.source,cr.external_id,cr.name,cr.code,
               cr.release_type,cr.release_date,cr.language,cr.region
        FROM print_releases pr
        JOIN catalog_releases cr ON cr.id=pr.release_id
        WHERE pr.print_id=:print_id
        ORDER BY cr.release_date NULLS LAST,cr.id
        """
    )
    try:
        with db.SessionLocal() as session:
            identity = session.execute(identity_sql, {"print_id": print_id}).mappings().first()
            if identity is None:
                return jsonify({"error": "not_found", "detail": f"print {print_id} not found"}), 404
            releases = [_release_payload(dict(row)) for row in session.execute(releases_sql, {"print_id": print_id}).mappings().all()]
    except SQLAlchemyError:
        return jsonify({"error": "print_physical_releases_unavailable"}), 503

    return jsonify({
        "print": dict(identity),
        "physical_releases": releases,
        "physical_release_names": [release["name"] for release in releases if release.get("name")],
    })
