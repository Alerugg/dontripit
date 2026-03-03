from flask import Blueprint, jsonify
from sqlalchemy import select, text

from app import db
from app.models import Game

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

    return jsonify([{"id": row.id, "slug": row.slug, "name": row.name} for row in rows])
