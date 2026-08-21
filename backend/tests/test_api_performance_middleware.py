from sqlalchemy import func, select

from app import db
from app.models import ApiRequestMetric


def _metric_count() -> int:
    with db.SessionLocal() as session:
        return int(session.execute(select(func.count()).select_from(ApiRequestMetric)).scalar_one())


def test_vercel_requests_do_not_block_on_metrics_database_write(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("API_REQUEST_METRICS_DB_ENABLED", raising=False)
    before = _metric_count()

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.headers["Server-Timing"].startswith("app;dur=")
    assert response.headers["X-App-Response-Time-Ms"].isdigit()
    assert _metric_count() == before


def test_database_metrics_can_be_explicitly_enabled_on_vercel(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("API_REQUEST_METRICS_DB_ENABLED", "true")
    before = _metric_count()

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert _metric_count() == before + 1
