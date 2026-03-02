from flask import Blueprint

from app.routes.catalog import list_prints, print_detail, search_catalog, list_sets
from app.routes.games import list_games
from app.routes.health import health

v1_bp = Blueprint("v1", __name__, url_prefix="/api/v1")

v1_bp.add_url_rule("/health", view_func=health, methods=["GET"])
v1_bp.add_url_rule("/search", view_func=search_catalog, methods=["GET"])
v1_bp.add_url_rule("/games", view_func=list_games, methods=["GET"])
v1_bp.add_url_rule("/sets", view_func=list_sets, methods=["GET"])
v1_bp.add_url_rule("/prints", view_func=list_prints, methods=["GET"])
v1_bp.add_url_rule("/prints/<int:print_id>", view_func=print_detail, methods=["GET"])
