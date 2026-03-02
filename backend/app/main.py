from flask import Flask

from app.db import init_engine
from app.routes.games import games_bp
from app.routes.health import health_bp


def create_app(database_url: str | None = None) -> Flask:
    init_engine(database_url)
    flask_app = Flask(__name__)
    flask_app.register_blueprint(health_bp)
    flask_app.register_blueprint(games_bp)
    return flask_app


app = create_app()
