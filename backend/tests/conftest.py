import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.main import create_app
from app.auth import middleware
from app.models import Base
from app.routes import catalog


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    os.environ["DATABASE_URL"] = database_url

    if db.engine is not None:
        db.engine.dispose()
    db.init_engine(database_url)

    os.environ["PUBLIC_API_ENABLED"] = "false"
    app = create_app(database_url=database_url)
    app.config["RATE_LIMIT_PER_MINUTE"] = 5
    app.config["CACHE_TTL_SECONDS"] = 60
    catalog._RATE_LIMIT_BUCKETS.clear()
    catalog._CACHE.clear()
    middleware._RATE_WINDOWS.clear()
    Base.metadata.drop_all(bind=db.engine)
    Base.metadata.create_all(bind=db.engine)

    with app.test_client() as test_client:
        yield test_client

    if db.engine is not None:
        db.engine.dispose()
