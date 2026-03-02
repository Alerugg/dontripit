from flask import Blueprint, jsonify, request
from sqlalchemy import and_, func, or_, select, text

from app import db
from app.models import Card, Game, Print, PrintIdentifier, PrintImage, Set

catalog_bp = Blueprint("catalog", __name__)


TRUE_VALUES = {"1", "true", "t", "yes", "y", "on"}
FALSE_VALUES = {"0", "false", "f", "no", "n", "off"}


def _parse_pagination(default_limit: int = 50, max_limit: int = 200) -> tuple[int, int]:
    limit_raw = request.args.get("limit", str(default_limit))
    offset_raw = request.args.get("offset", "0")

    try:
        limit = int(limit_raw)
    except ValueError:
        limit = default_limit
    try:
        offset = int(offset_raw)
    except ValueError:
        offset = 0

    limit = max(1, min(limit, max_limit))
    offset = max(0, offset)
    return limit, offset


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    lowered = value.strip().lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    return None


def _print_to_summary(row: Print, card_name: str, set_code: str, set_name: str, primary_image_url: str | None):
    return {
        "id": row.id,
        "card_id": row.card_id,
        "set_id": row.set_id,
        "collector_number": row.collector_number,
        "language": row.language,
        "rarity": row.rarity,
        "is_foil": row.is_foil,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "card": {"name": card_name},
        "set": {"code": set_code, "name": set_name},
        "primary_image_url": primary_image_url,
    }


@catalog_bp.get("/api/sets")
def list_sets():
    game_slug = request.args.get("game")
    sort = request.args.get("sort", "release_date_desc")

    with db.SessionLocal() as session:
        query = select(Set).join(Game, Set.game_id == Game.id)
        if game_slug:
            query = query.where(Game.slug == game_slug)

        if sort == "name":
            query = query.order_by(Set.name.asc())
        else:
            query = query.order_by(Set.release_date.desc().nullslast(), Set.name.asc())

        rows = session.execute(query).scalars().all()

    return jsonify(
        [
            {
                "id": row.id,
                "game_id": row.game_id,
                "code": row.code,
                "name": row.name,
                "release_date": row.release_date.isoformat() if row.release_date else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@catalog_bp.get("/api/cards")
def list_cards():
    game_slug = request.args.get("game")
    search = request.args.get("q")
    limit, offset = _parse_pagination()

    with db.SessionLocal() as session:
        query = select(Card).join(Game, Card.game_id == Game.id)
        if game_slug:
            query = query.where(Game.slug == game_slug)
        if search:
            query = query.where(Card.name.ilike(f"%{search}%"))
        query = query.order_by(Card.name.asc()).limit(limit).offset(offset)
        rows = session.execute(query).scalars().all()

    return jsonify(
        [
            {
                "id": row.id,
                "game_id": row.game_id,
                "name": row.name,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    )


@catalog_bp.get("/api/prints")
def list_prints():
    game_slug = request.args.get("game")
    set_code = request.args.get("set_code")
    search = request.args.get("q")
    language = request.args.get("language")
    rarity = request.args.get("rarity")
    collector_number = request.args.get("collector_number")
    is_foil = _parse_bool(request.args.get("is_foil"))
    limit, offset = _parse_pagination()

    primary_image_subquery = (
        select(PrintImage.print_id, func.min(PrintImage.url).label("primary_image_url"))
        .where(PrintImage.is_primary.is_(True))
        .group_by(PrintImage.print_id)
        .subquery()
    )

    with db.SessionLocal() as session:
        query = (
            select(Print, Card.name, Set.code, Set.name, primary_image_subquery.c.primary_image_url)
            .join(Card, Print.card_id == Card.id)
            .join(Set, Print.set_id == Set.id)
            .join(Game, Card.game_id == Game.id)
            .outerjoin(primary_image_subquery, primary_image_subquery.c.print_id == Print.id)
        )

        filters = []
        if game_slug:
            filters.append(Game.slug == game_slug)
        if set_code:
            filters.append(Set.code == set_code)
        if search:
            like_term = f"%{search}%"
            filters.append(or_(Card.name.ilike(like_term), Set.name.ilike(like_term)))
        if language:
            filters.append(Print.language == language)
        if rarity:
            filters.append(Print.rarity == rarity)
        if collector_number:
            filters.append(Print.collector_number == collector_number)
        if is_foil is not None:
            filters.append(Print.is_foil.is_(is_foil))
        if filters:
            query = query.where(and_(*filters))

        query = query.order_by(Set.code.asc(), Print.collector_number.asc(), Card.name.asc()).limit(limit).offset(offset)
        rows = session.execute(query).all()

    return jsonify([_print_to_summary(*row) for row in rows])


@catalog_bp.get("/api/search")
def search():
    query_text = request.args.get("q", "").strip()
    if len(query_text) < 2:
        return jsonify({"error": "q must be at least 2 characters"}), 400

    result_type = request.args.get("type")
    if result_type not in {None, "card", "set", "print"}:
        return jsonify({"error": "type must be one of: card, set, print"}), 400

    game_slug = request.args.get("game")
    limit, offset = _parse_pagination(default_limit=20, max_limit=100)

    with db.SessionLocal() as session:
        if session.bind.dialect.name == "postgresql":
            rows = session.execute(
                text(
                    """
                    SELECT
                        sd.doc_type AS type,
                        sd.object_id AS id,
                        sd.title,
                        sd.subtitle,
                        ts_rank(sd.tsv, plainto_tsquery('simple', :q)) AS score,
                        s.code AS set_code,
                        p.collector_number,
                        pi.primary_image_url
                    FROM search_documents sd
                    JOIN games g ON g.id = sd.game_id
                    LEFT JOIN prints p ON sd.doc_type = 'print' AND p.id = sd.object_id
                    LEFT JOIN sets s ON p.set_id = s.id
                    LEFT JOIN (
                        SELECT print_id, MIN(url) AS primary_image_url
                        FROM print_images
                        WHERE is_primary IS TRUE
                        GROUP BY print_id
                    ) pi ON pi.print_id = p.id
                    WHERE sd.tsv @@ plainto_tsquery('simple', :q)
                      AND (:type IS NULL OR sd.doc_type = :type)
                      AND (:game IS NULL OR g.slug = :game)
                    ORDER BY score DESC, sd.title ASC
                    LIMIT :limit OFFSET :offset
                    """
                ),
                {"q": query_text, "type": result_type, "game": game_slug, "limit": limit, "offset": offset},
            ).mappings().all()
            payload = []
            for row in rows:
                item = {
                    "type": row["type"],
                    "id": row["id"],
                    "title": row["title"],
                    "subtitle": row["subtitle"],
                    "score": float(row["score"] or 0),
                }
                if row["type"] == "print":
                    item["set_code"] = row["set_code"]
                    item["collector_number"] = row["collector_number"]
                    item["primary_image_url"] = row["primary_image_url"]
                payload.append(item)
            return jsonify(payload)

        # SQLite/test fallback: emulate search semantics with ILIKE.
        like_term = f"%{query_text}%"
        payload = []
        if result_type in {None, "card"}:
            card_query = select(Card.id, Card.name, Game.slug).join(Game, Game.id == Card.game_id).where(Card.name.ilike(like_term))
            if game_slug:
                card_query = card_query.where(Game.slug == game_slug)
            for card_id, name, _ in session.execute(card_query):
                payload.append({"type": "card", "id": card_id, "title": name, "subtitle": None, "score": 1.0})

        if result_type in {None, "set"}:
            set_query = (
                select(Set.id, Set.name, Set.code, Game.slug)
                .join(Game, Game.id == Set.game_id)
                .where(or_(Set.name.ilike(like_term), Set.code.ilike(like_term)))
            )
            if game_slug:
                set_query = set_query.where(Game.slug == game_slug)
            for set_id, name, code, _ in session.execute(set_query):
                payload.append({"type": "set", "id": set_id, "title": name, "subtitle": code, "score": 1.0})

        if result_type in {None, "print"}:
            primary_image_subquery = (
                select(PrintImage.print_id, func.min(PrintImage.url).label("primary_image_url"))
                .where(PrintImage.is_primary.is_(True))
                .group_by(PrintImage.print_id)
                .subquery()
            )
            print_query = (
                select(Print.id, Card.name, Set.code, Print.collector_number, primary_image_subquery.c.primary_image_url, Game.slug)
                .join(Card, Card.id == Print.card_id)
                .join(Set, Set.id == Print.set_id)
                .join(Game, Game.id == Card.game_id)
                .outerjoin(primary_image_subquery, primary_image_subquery.c.print_id == Print.id)
                .where(
                    or_(
                        Card.name.ilike(like_term),
                        Set.name.ilike(like_term),
                        Set.code.ilike(like_term),
                        Print.collector_number.ilike(like_term),
                    )
                )
            )
            if game_slug:
                print_query = print_query.where(Game.slug == game_slug)
            for print_id, card_name, set_code, collector_number, primary_image_url, _ in session.execute(print_query):
                payload.append(
                    {
                        "type": "print",
                        "id": print_id,
                        "title": card_name,
                        "subtitle": f"{set_code} #{collector_number}",
                        "score": 1.0,
                        "set_code": set_code,
                        "collector_number": collector_number,
                        "primary_image_url": primary_image_url,
                    }
                )

        payload = sorted(payload, key=lambda x: (-x["score"], x["title"]))[offset : offset + limit]
        return jsonify(payload)


@catalog_bp.get("/api/prints/<int:print_id>")
def print_detail(print_id: int):
    with db.SessionLocal() as session:
        game_and_print = (
            session.execute(
                select(Print, Card, Set)
                .join(Card, Print.card_id == Card.id)
                .join(Set, Print.set_id == Set.id)
                .where(Print.id == print_id)
            )
            .one_or_none()
        )

        if game_and_print is None:
            return jsonify({"error": "print not found"}), 404

        print_row, card_row, set_row = game_and_print
        images = (
            session.execute(
                select(PrintImage)
                .where(PrintImage.print_id == print_id)
                .order_by(PrintImage.is_primary.desc(), PrintImage.id.asc())
            )
            .scalars()
            .all()
        )
        identifiers = (
            session.execute(
                select(PrintIdentifier)
                .where(PrintIdentifier.print_id == print_id)
                .order_by(PrintIdentifier.id.asc())
            )
            .scalars()
            .all()
        )

    payload = {
        "print": {
            "id": print_row.id,
            "card_id": print_row.card_id,
            "set_id": print_row.set_id,
            "collector_number": print_row.collector_number,
            "language": print_row.language,
            "rarity": print_row.rarity,
            "is_foil": print_row.is_foil,
            "created_at": print_row.created_at.isoformat() if print_row.created_at else None,
        },
        "card": {"id": card_row.id, "game_id": card_row.game_id, "name": card_row.name},
        "set": {
            "id": set_row.id,
            "game_id": set_row.game_id,
            "code": set_row.code,
            "name": set_row.name,
            "release_date": set_row.release_date.isoformat() if set_row.release_date else None,
        },
        "images": [
            {
                "id": image.id,
                "url": image.url,
                "is_primary": image.is_primary,
                "source": image.source,
            }
            for image in images
        ],
        "identifiers": [
            {"id": identifier.id, "source": identifier.source, "external_id": identifier.external_id}
            for identifier in identifiers
        ],
    }
    return jsonify(payload)
