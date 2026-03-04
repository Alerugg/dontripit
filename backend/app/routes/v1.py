from flask import Blueprint

from app.routes.catalog import get_card_detail, get_print_detail, list_cards, list_prints, list_product_variants, list_products, list_sets, product_detail
from app.routes.games import list_games
from app.routes.health import health
from app.routes.search import search

v1_bp = Blueprint("v1", __name__, url_prefix="/api/v1")

v1_bp.add_url_rule("/health", view_func=health, methods=["GET"])
v1_bp.add_url_rule("/search", view_func=search, methods=["GET"])
v1_bp.add_url_rule("/games", view_func=list_games, methods=["GET"])
v1_bp.add_url_rule("/sets", view_func=list_sets, methods=["GET"])
v1_bp.add_url_rule("/cards", view_func=list_cards, methods=["GET"])
v1_bp.add_url_rule("/prints", view_func=list_prints, methods=["GET"])
v1_bp.add_url_rule("/cards/<int:card_id>", view_func=get_card_detail, methods=["GET"])
v1_bp.add_url_rule("/prints/<int:print_id>", view_func=get_print_detail, methods=["GET"])
v1_bp.add_url_rule("/products", view_func=list_products, methods=["GET"])
v1_bp.add_url_rule("/products/<int:product_id>", view_func=product_detail, methods=["GET"])
v1_bp.add_url_rule("/product-variants", view_func=list_product_variants, methods=["GET"])
