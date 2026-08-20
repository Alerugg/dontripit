import logging
import os

from flask import Flask, jsonify
from sqlalchemy.exc import OperationalError, ProgrammingError
from werkzeug.exceptions import HTTPException

from app.auth import register_api_product_middleware
from app.auth.service import ensure_active_api_key
from app.db import init_engine
from app import db
from app.routes.catalog import catalog_bp
from app.routes.card_prints import card_prints_bp
from app.routes.admin import admin_bp
from app.routes.admin_ingest import admin_ingest_bp
from app.routes.admin_ingest_status import admin_ingest_status_bp
from app.routes.admin_refresh import admin_refresh_bp
from app.routes.admin_seed import admin_seed_bp
from app.routes.admin_metrics import admin_metrics_bp
from app.routes.docs import docs_bp
from app.routes.games import games_bp
from app.routes.health import health_bp
from app.routes.market_current_products import market_current_products_bp
from app.routes.market_reference import market_reference_bp
from app.routes.market_search_read import market_search_read_bp
from app.routes.market_print_summary import market_print_summary_bp
from app.routes.product_media import product_media_bp
from app.routes.search import search_bp
from app.routes.search_v2 import search_v2_bp
from app.routes.prices import prices_bp
from app.routes.set_ui import set_ui_bp
from app.routes.user_auth import user_auth_bp
from app.routes.user_library import user_library_bp


logger = logging.getLogger(__name__)


def create_app(database_url: str | None = None) -> Flask:
    init_engine(database_url)
    local_internal_api_key = os.getenv("INTERNAL_API_KEY", "").strip()
    if local_internal_api_key:
        try:
            with db.SessionLocal() as session:
                ensure_active_api_key(
                    session,
                    local_internal_api_key,
                    plan_name=os.getenv("INTERNAL_API_PLAN", "free"),
                    label=os.getenv("INTERNAL_API_LABEL", "local-internal"),
                )
        except (OperationalError, ProgrammingError):
            # During first boot before migrations, API product tables may not exist yet.
            pass

    flask_app = Flask(__name__)
    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(games_bp)
    flask_app.register_blueprint(catalog_bp)
    flask_app.register_blueprint(card_prints_bp)
    flask_app.register_blueprint(set_ui_bp)
    flask_app.register_blueprint(search_bp)
    flask_app.register_blueprint(search_v2_bp)
    flask_app.register_blueprint(market_current_products_bp)
    flask_app.register_blueprint(market_reference_bp)
    flask_app.register_blueprint(market_search_read_bp)
    flask_app.register_blueprint(market_print_summary_bp)
    flask_app.register_blueprint(product_media_bp)
    flask_app.register_blueprint(user_auth_bp)
    flask_app.register_blueprint(user_library_bp)
    flask_app.register_blueprint(docs_bp)
    flask_app.register_blueprint(admin_metrics_bp)
    flask_app.register_blueprint(admin_bp)
    flask_app.register_blueprint(admin_ingest_bp)
    flask_app.register_blueprint(admin_ingest_status_bp)
    flask_app.register_blueprint(admin_refresh_bp)
    flask_app.register_blueprint(admin_seed_bp)
    flask_app.register_blueprint(prices_bp)
    register_api_product_middleware(flask_app)

    @flask_app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return jsonify({"error": error.name.lower().replace(" ", "_"), "detail": error.description}), error.code

    @flask_app.errorhandler(Exception)
    def handle_uncaught(error: Exception):
        logger.exception("Unhandled application error", exc_info=error)
        return jsonify({"error": "internal_server_error"}), 500

    return flask_app


app = create_app()
