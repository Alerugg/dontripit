from __future__ import annotations


def test_vercel_catalog_get_is_public_even_if_legacy_api_switch_is_off(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")
    monkeypatch.delenv("PUBLIC_HUB_CATALOG_ENABLED", raising=False)

    response = client.get("/api/v1/db-check")

    assert response.status_code == 200
    assert response.get_json() == {"db": "ok"}
    assert response.headers["X-Plan"] == "public"


def test_vercel_catalog_get_ignores_stale_shared_key_and_uses_public_rate_limit(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")
    monkeypatch.delenv("PUBLIC_HUB_CATALOG_ENABLED", raising=False)

    response = client.get("/api/v1/db-check", headers={"X-API-Key": "ak_stale_railway_key"})

    assert response.status_code == 200
    assert response.get_json() == {"db": "ok"}
    assert response.headers["X-Plan"] == "public"


def test_vercel_admin_route_remains_private(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    monkeypatch.delenv("PUBLIC_HUB_CATALOG_ENABLED", raising=False)

    response = client.get("/api/v1/admin/ingest-status")

    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}


def test_vercel_non_safe_catalog_method_remains_private(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("PUBLIC_API_ENABLED", "true")
    monkeypatch.delenv("PUBLIC_HUB_CATALOG_ENABLED", raising=False)

    response = client.post("/api/v1/db-check")

    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}


def test_explicit_public_hub_kill_switch_wins_on_vercel(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("PUBLIC_HUB_CATALOG_ENABLED", "false")

    response = client.get("/api/v1/db-check")

    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}
