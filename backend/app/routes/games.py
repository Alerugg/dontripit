from flask import Blueprint, jsonify
from sqlalchemy import func, select, text

from app import db
from app.models import Card, Game

games_bp = Blueprint("games", __name__)


@games_bp.get("/api/db-check")
@games_bp.get("/api/v1/db-check")
def db_check():
    try:
        with db.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return jsonify({"db": "ok"})
    except Exception as exc:
        return jsonify({"error": str(exc).splitlines()[0]}), 500


@games_bp.get("/api/games")
@games_bp.get("/api/v1/games")
def list_games():
    with db.SessionLocal() as session:
        rows = session.execute(select(Game).order_by(Game.id)).scalars().all()
        visible_rows = []
        for row in rows:
            if row.slug != "riftbound":
                visible_rows.append(row)
                continue
            has_cards = session.execute(select(func.count(Card.id)).where(Card.game_id == row.id)).scalar_one()
            if has_cards > 0:
                visible_rows.append(row)

    return jsonify([{"id": row.id, "slug": row.slug, "name": row.name} for row in visible_rows])
