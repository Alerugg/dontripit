from flask import g
from sqlalchemy import select

from app import db
from app.models import RateLimitBucket
from app.routes import catalog
from app.rate_limit import clear_memory_rate_limits


def test_uncaught_exception_does_not_leak_internal_detail(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")

    def explode():
        raise RuntimeError("postgresql://secret-user:secret-password@private-host/db")

    app = client.application
    app.add_url_rule("/api/security-test-explode", endpoint="security_test_explode", view_func=explode)
    response = client.get("/api/security-test-explode")

    assert response.status_code == 500
    assert response.get_json() == {"error": "internal_server_error"}
    assert b"secret-password" not in response.data


def test_catalog_5xx_helper_hides_database_detail(client):
    with client.application.test_request_context("/api/v2/catalog/cards/1"):
        response, status = catalog._json_error(
            "card_detail_failed",
            "relation private_table does not exist",
            500,
        )

    assert status == 500
    assert response.get_json() == {"error": "card_detail_failed"}


def test_search_v2_caps_limit_before_query_execution(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    captured = {}

    def fake_search(_session, *, query, game, limit):
        captured.update(query=query, game=game, limit=limit)
        return []

    monkeypatch.setattr("app.routes.search_v2._normal_search_for_game", fake_search)
    response = client.get("/api/v2/search?q=a&limit=999999")

    assert response.status_code == 200
    assert captured["limit"] == 50


def test_search_v2_truncates_oversized_query(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    captured = {}

    def fake_search(_session, *, query, game, limit):
        captured["query"] = query
        return []

    monkeypatch.setattr("app.routes.search_v2._normal_search_for_game", fake_search)
    response = client.get("/api/v2/search", query_string={"q": "x" * 10_000})

    assert response.status_code == 200
    assert len(captured["query"]) == 200


def test_public_rate_limit_is_shared_in_database_not_worker_memory(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    monkeypatch.setenv("PUBLIC_IP_RATE_LIMIT_RPM", "1")
    headers = {"X-Forwarded-For": "203.0.113.201"}

    first = client.get("/api/games", headers=headers)
    clear_memory_rate_limits()  # Simulate another worker with empty RAM.
    second = client.get("/api/games", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {"error": "rate_limited"}
    assert int(second.headers["Retry-After"]) >= 1

    with db.SessionLocal() as session:
        bucket = session.execute(select(RateLimitBucket)).scalar_one()
        assert bucket.identity_hash != "203.0.113.201"
        assert len(bucket.identity_hash) == 64


def test_public_catalog_pagination_is_human_scale(client):
    with client.application.test_request_context("/api/v1/cards?limit=9999&offset=999999"):
        g.api_meta = {"plan": "public"}
        assert catalog._pagination() == (50, 1_000)


def test_scoped_api_catalog_keeps_deeper_pagination(client):
    with client.application.test_request_context("/api/v1/cards?limit=9999&offset=999999"):
        g.api_meta = {"plan": "pro"}
        assert catalog._pagination() == (200, 999_999)
