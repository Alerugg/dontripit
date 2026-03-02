from flask import Blueprint, jsonify, request
from sqlalchemy import text

from app import db

catalog_bp = Blueprint("catalog", __name__)


def _int_param(name: str, default: int, maximum: int) -> int:
    value = request.args.get(name, default=default, type=int)
    if value is None:
        return default
    return min(max(value, 0), maximum)


@catalog_bp.get("/api/cards")
def list_cards():
    q = request.args.get("q", "").strip()
    game = request.args.get("game", "").strip()
    limit = _int_param("limit", 20, 100)
    offset = _int_param("offset", 0, 100000)

    where = []
    params = {"limit": limit, "offset": offset}
    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if q:
        where.append("LOWER(c.name) LIKE :q")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT c.id, c.name, g.slug AS game
        FROM cards c
        JOIN games g ON g.id = c.game_id
        {where_sql}
        ORDER BY c.name, c.id
        LIMIT :limit OFFSET :offset
        """
    )

    with db.SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    result = [dict(row) for row in rows]
    return jsonify(result)


@catalog_bp.get("/api/prints")
def list_prints():
    q = request.args.get("q", "").strip()
    game = request.args.get("game", "").strip()
    limit = _int_param("limit", 20, 100)
    offset = _int_param("offset", 0, 100000)

    where = []
    params = {"limit": limit, "offset": offset}
    if game:
        where.append("g.slug = :game")
        params["game"] = game
    if q:
        where.append("(LOWER(c.name) LIKE :q OR LOWER(p.collector_number) LIKE :q)")
        params["q"] = f"%{q.lower()}%"

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    sql = text(
        f"""
        SELECT p.id, c.name AS card_name, s.code AS set_code, p.collector_number
        FROM prints p
        JOIN cards c ON c.id = p.card_id
        JOIN sets s ON s.id = p.set_id
        JOIN games g ON g.id = s.game_id
        {where_sql}
        ORDER BY s.code, p.collector_number, p.id
        LIMIT :limit OFFSET :offset
        """
    )

    with db.SessionLocal() as session:
        rows = session.execute(sql, params).mappings().all()
    result = [dict(row) for row in rows]
    return jsonify(result)
