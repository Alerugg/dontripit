from flask import Flask

from app.db import init_engine
from app.routes.catalog import catalog_bp
from app.routes.games import games_bp
from app.routes.health import health_bp
from app.routes.v1 import v1_bp


def create_app(database_url: str | None = None) -> Flask:
    init_engine(database_url)
    flask_app = Flask(__name__)
    flask_app.config.setdefault("RATE_LIMIT_PER_MINUTE", 60)
    flask_app.config.setdefault("CACHE_TTL_SECONDS", 30)

    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(games_bp)
    flask_app.register_blueprint(catalog_bp)

    flask_app.register_blueprint(v1_bp)
    return flask_app


app = create_app()
