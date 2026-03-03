from flask import Flask, jsonify
from werkzeug.exceptions import HTTPException

from app.auth import register_api_product_middleware
from app.db import init_engine
from app.routes.catalog import catalog_bp
from app.routes.admin_ingest import admin_ingest_bp
from app.routes.admin_metrics import admin_metrics_bp
from app.routes.docs import docs_bp
from app.routes.games import games_bp
from app.routes.health import health_bp
from app.routes.search import search_bp
from app.routes.prices import prices_bp


def create_app(database_url: str | None = None) -> Flask:
    init_engine(database_url)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(games_bp)
    flask_app.register_blueprint(catalog_bp)
    flask_app.register_blueprint(search_bp)
    flask_app.register_blueprint(docs_bp)
    flask_app.register_blueprint(admin_metrics_bp)
    flask_app.register_blueprint(admin_ingest_bp)
    flask_app.register_blueprint(prices_bp)
    register_api_product_middleware(flask_app)

    @flask_app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return jsonify({"error": error.name.lower().replace(" ", "_"), "detail": error.description}), error.code

    @flask_app.errorhandler(Exception)
    def handle_uncaught(error: Exception):
        return jsonify({"error": "internal_server_error", "detail": str(error)}), 500

    return flask_app


app = create_app()
