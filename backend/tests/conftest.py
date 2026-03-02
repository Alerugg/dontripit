import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.main import create_app
from app.models import Base


@pytest.fixture()
def client(tmp_path):
    db_path = tmp_path / "test.db"
    database_url = f"sqlite+pysqlite:///{db_path}"
    os.environ["DATABASE_URL"] = database_url

    app = create_app(database_url=database_url)
    Base.metadata.create_all(bind=db.engine)

    with app.test_client() as test_client:
        yield test_client
