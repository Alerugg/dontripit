from app import db
from app.routes import catalog


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
    assert captured["limit"] == 100


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
