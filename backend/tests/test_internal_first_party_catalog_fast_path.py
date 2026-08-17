from __future__ import annotations

from flask import Flask, jsonify

from app.auth import middleware


def _app() -> Flask:
    app = Flask(__name__)
    middleware.register_api_product_middleware(app)

    @app.get("/api/v2/search/suggest")
    def safe_get():
        return jsonify({"ok": True})

    @app.post("/api/v2/search/advanced")
    def safe_post():
        return jsonify({"ok": True})

    @app.post("/api/v2/not-read-only")
    def unsafe_post():
        return jsonify({"ok": True})

    return app


def _fail_db_auth(*args, **kwargs):
    raise AssertionError("first-party safe catalog request touched DB auth/rate-limit path")


def test_internal_key_fast_paths_safe_catalog_get_without_db_auth(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "first-party-secret")
    monkeypatch.setattr(middleware, "find_active_key", _fail_db_auth)
    monkeypatch.setattr(middleware, "consume_rate_limit", _fail_db_auth)

    response = _app().test_client().get(
        "/api/v2/search/suggest?q=Pikachu&game=pokemon",
        headers={"X-API-Key": "first-party-secret"},
    )

    assert response.status_code == 200
    assert response.headers["X-Plan"] == "internal-first-party"
    assert response.headers["X-Quota-Monthly"] == "unlimited"


def test_internal_key_fast_paths_allowlisted_read_only_post_without_db_auth(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "first-party-secret")
    monkeypatch.setattr(middleware, "find_active_key", _fail_db_auth)
    monkeypatch.setattr(middleware, "consume_rate_limit", _fail_db_auth)

    response = _app().test_client().post(
        "/api/v2/search/advanced",
        json={"game": "pokemon", "q": "Pikachu", "filters": {}},
        headers={"Authorization": "Bearer first-party-secret"},
    )

    assert response.status_code == 200
    assert response.headers["X-Plan"] == "internal-first-party"


def test_internal_key_does_not_fast_path_unallowlisted_post(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "first-party-secret")
    touched = {"find": False}

    def fake_find_active_key(session, provided_key):
        touched["find"] = True
        return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(middleware.db, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(middleware, "find_active_key", fake_find_active_key)

    response = _app().test_client().post(
        "/api/v2/not-read-only",
        json={},
        headers={"X-API-Key": "first-party-secret"},
    )

    assert touched["find"] is True
    assert response.status_code == 401


def test_wrong_key_never_uses_internal_fast_path(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_KEY", "first-party-secret")
    touched = {"find": False}

    def fake_find_active_key(session, provided_key):
        touched["find"] = True
        return None

    class _Session:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(middleware.db, "SessionLocal", lambda: _Session())
    monkeypatch.setattr(middleware, "find_active_key", fake_find_active_key)
    monkeypatch.setenv("PUBLIC_HUB_CATALOG_ENABLED", "false")

    response = _app().test_client().get(
        "/api/v2/search/suggest?q=Pikachu",
        headers={"X-API-Key": "wrong-secret"},
    )

    assert touched["find"] is True
    assert response.status_code == 401
