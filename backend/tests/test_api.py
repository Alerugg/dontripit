import os

from sqlalchemy import func, select

from app import db
from app.auth import middleware
from app.auth.create_key import main as create_key_main
from app.auth.service import disable_key_by_prefix, hash_api_key, rotate_key_by_prefix
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, PriceSnapshot, Print, Product, SourceRecord
from app.scripts.seed import run_seed


def _auth_headers(key: str = "test-key", scopes: list[str] | None = None) -> dict[str, str]:
    with db.SessionLocal() as session:
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == "free")).scalar_one_or_none()
        if plan is None:
            plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
            session.add(plan)
            session.flush()

        api_key = session.execute(select(ApiKey).where(ApiKey.prefix == key[:8])).scalar_one_or_none()
        if api_key is None:
            session.add(
                ApiKey(
                    key_hash=hash_api_key(key),
                    prefix=key[:8],
                    plan_id=plan.id,
                    is_active=True,
                    scopes=scopes or ["read:catalog"],
                )
            )
            session.commit()

    return {"X-API-Key": key}


def test_allows_health_without_key(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_db_check(client):
    response = client.get("/api/db-check", headers=_auth_headers())
    assert response.status_code == 200
    assert response.get_json() == {"db": "ok"}


def test_games(client):
    run_seed()
    response = client.get("/api/games", headers=_auth_headers())
    assert response.status_code == 200
    data = response.get_json()
    slugs = {item["slug"] for item in data}
    assert {"pokemon", "mtg"}.issubset(slugs)


def test_versioning_alias_games_matches_v1(client):
    run_seed()
    headers = _auth_headers()
    legacy = client.get("/api/games", headers=headers)
    v1 = client.get("/api/v1/games", headers=headers)
    assert legacy.status_code == 200
    assert v1.status_code == 200
    assert legacy.get_json() == v1.get_json()


def test_search_works(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_search_returns_results_after_seed_or_ingest(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] in {"card", "print"} for item in payload)


def test_search_with_empty_type_and_no_results_returns_200(client):
    response = client.get("/api/search?q=zzzz-no-results&game=pokemon&type=", headers=_auth_headers())
    assert response.status_code == 200
    assert response.get_json() == []


def test_cards_returns_200_and_json_list(client):
    run_seed()
    response = client.get("/api/cards", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_prints_returns_200_and_json_list(client):
    run_seed()
    response = client.get("/api/prints", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)


def test_ingest_fixture_local_idempotent(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_count_first = session.execute(select(func.count(Print.id))).scalar_one()
        source_records_first = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_count_second = session.execute(select(func.count(Print.id))).scalar_one()
        source_records_second = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    assert print_count_first == print_count_second
    assert source_records_first == source_records_second


def test_sets_list_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/sets?game=pokemon", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload, list)
    assert any(item["code"] == "SV1" for item in payload)


def test_print_detail_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    with db.SessionLocal() as session:
        print_id = session.execute(select(Print.id).order_by(Print.id)).scalars().first()

    response = client.get(f"/api/prints/{print_id}", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["print"]["id"] == print_id
    assert payload["card"]["name"]
    assert payload["set"]["code"]
    assert isinstance(payload["images"], list)


def test_search_returns_pikachu(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=pika&game=pokemon&type=card", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] == "card" for item in payload)


def test_search_returns_print_by_collector_number(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    response = client.get("/api/search?q=001&game=pokemon&type=print", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] == "print" and item.get("collector_number") == "001" for item in payload)


def test_public_enabled_allows_no_key_with_ip_rate_limit(client):
    os.environ["PUBLIC_API_ENABLED"] = "true"
    response = client.get("/api/games")
    assert response.status_code == 200


def test_requires_key_when_public_disabled(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/games")
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}


def test_allows_with_valid_key(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"

    with db.SessionLocal() as session:
        plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
        session.add(plan)
        session.flush()
        api_key = ApiKey(
            key_hash=hash_api_key("test-key"),
            prefix="test-key",
            plan_id=plan.id,
            is_active=True,
            scopes=["read:catalog"],
        )
        session.add(api_key)
        session.commit()

    response = client.get("/api/games", headers={"X-API-Key": "test-key"})
    assert response.status_code == 200


def test_metrics_requires_admin_scope(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/v1/admin/metrics", headers=_auth_headers("catalog-key", ["read:catalog"]))
    assert response.status_code == 403
    assert response.get_json() == {"error": "insufficient_scope"}


def test_metrics_allows_admin_scope(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    headers = _auth_headers("admin-key", ["read:catalog", "read:admin"])
    client.get("/api/games", headers=headers)
    response = client.get("/api/v1/admin/metrics", headers=headers)
    assert response.status_code == 200
    payload = response.get_json()
    assert "requests_total" in payload
    assert "average_latency_ms" in payload
    assert "top_api_keys_month" in payload


def test_admin_ingest_status_allows_admin_scope(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures")
        session.commit()

    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/v1/admin/ingest-status", headers=_auth_headers("admin-ing", ["read:catalog", "read:admin"]))
    assert response.status_code == 200

    payload = response.get_json()
    assert set(payload.keys()) == {"sources", "games", "now"}
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["games"], list)

    if payload["sources"]:
        assert set(payload["sources"][0].keys()) == {"name", "last_run_at", "records"}
    if payload["games"]:
        assert set(payload["games"][0].keys()) == {"slug", "cards", "sets", "prints"}


def test_admin_ingest_status_requires_key(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/v1/admin/ingest-status")
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}


def test_create_key_cli_creates_record(client, monkeypatch, capsys):
    with db.SessionLocal() as session:
        session.add(ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60))
        session.commit()

    monkeypatch.setattr("sys.argv", ["create_key", "--plan", "free", "--label", "dev"])
    create_key_main()
    created_key = capsys.readouterr().out.strip()

    with db.SessionLocal() as session:
        stored = session.execute(select(ApiKey).where(ApiKey.prefix == created_key[:8])).scalar_one()

    assert stored.key_hash == hash_api_key(created_key)
    assert stored.prefix == created_key[:8]
    assert stored.scopes == ["read:catalog"]


def test_disable_and_rotate_key_functions(client):
    with db.SessionLocal() as session:
        plan = ApiPlan(name="free", monthly_quota_requests=5000, burst_rpm=60)
        session.add(plan)
        session.flush()
        session.add(
            ApiKey(
                key_hash=hash_api_key("rotate-key"),
                prefix="rotate-k",
                plan_id=plan.id,
                is_active=True,
                scopes=["read:catalog"],
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        assert disable_key_by_prefix(session, "rotate-k") is True

    response = client.get("/api/games", headers={"X-API-Key": "rotate-key"})
    assert response.status_code == 401
    assert response.get_json() == {"error": "invalid_api_key"}

    with db.SessionLocal() as session:
        plan = session.execute(select(ApiPlan).where(ApiPlan.name == "free")).scalar_one()
        session.add(
            ApiKey(
                key_hash=hash_api_key("old-key"),
                prefix="old-key",
                plan_id=plan.id,
                is_active=True,
                scopes=["read:catalog"],
            )
        )
        session.commit()

    with db.SessionLocal() as session:
        generated = rotate_key_by_prefix(session, "old-key")
        assert generated is not None
        old = session.execute(select(ApiKey).where(ApiKey.prefix == "old-key")).scalar_one()
        new = session.execute(select(ApiKey).where(ApiKey.prefix == generated.prefix)).scalar_one()

    assert old.is_active is False
    assert new.is_active is True


def test_rate_limit_blocks(client):
    os.environ["PUBLIC_API_ENABLED"] = "true"
    os.environ["PUBLIC_IP_RATE_LIMIT_RPM"] = "1"
    middleware._RATE_WINDOWS.clear()

    first = client.get("/api/games")
    second = client.get("/api/games")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {"error": "rate_limited"}


def test_quota_exceeded_blocks(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    middleware._RATE_WINDOWS.clear()

    with db.SessionLocal() as session:
        plan = ApiPlan(name="free", monthly_quota_requests=1, burst_rpm=10)
        session.add(plan)
        session.flush()
        api_key = ApiKey(
            key_hash=hash_api_key("test-key"),
            prefix="test-key",
            plan_id=plan.id,
            is_active=True,
            scopes=["read:catalog"],
        )
        session.add(api_key)
        session.commit()

    first = client.get("/api/games", headers={"X-API-Key": "test-key"})
    second = client.get("/api/games", headers={"X-API-Key": "test-key"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.get_json() == {"error": "quota_exceeded"}


def test_products_list_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_products_demo.json")
        session.commit()

    response = client.get("/api/v1/products?game=pokemon&type=booster_box", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert isinstance(payload.get("items"), list)
    assert payload["total"] >= 1
    assert any(item["product_type"] == "booster_box" for item in payload["items"])


def test_product_detail_200(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_products_demo.json")
        session.commit()

    with db.SessionLocal() as session:
        product_id = session.execute(select(Product.id).order_by(Product.id)).scalars().first()

    response = client.get(f"/api/v1/products/{product_id}", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["product"]["id"] == product_id
    assert payload["variants"]
    assert payload["images"]


def test_ingest_products_idempotent(client):
    connector = get_connector("fixture_local")
    path = "data/fixtures/pokemon_products_demo.json"

    with db.SessionLocal() as session:
        connector.run(session, path)
        session.commit()

    with db.SessionLocal() as session:
        products_first = session.execute(select(func.count(Product.id))).scalar_one()
        source_records_first = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    with db.SessionLocal() as session:
        connector.run(session, path)
        session.commit()

    with db.SessionLocal() as session:
        products_second = session.execute(select(func.count(Product.id))).scalar_one()
        source_records_second = session.execute(select(func.count(SourceRecord.id))).scalar_one()

    assert products_first == products_second
    assert source_records_first == source_records_second


def test_price_ingest_fixture(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_demo.json")
        connector.run(session, "data/fixtures/prices_demo.json")
        session.commit()

    with db.SessionLocal() as session:
        total = session.execute(select(func.count(PriceSnapshot.id))).scalar_one()
    assert total >= 1


def test_prices_endpoint_returns_series(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_demo.json")
        connector.run(session, "data/fixtures/prices_demo.json")
        session.commit()

    response = client.get(
        "/api/v1/prices?entity_type=print&entity_id=1&source=demo&currency=EUR&granularity=raw",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["entity"]["id"] == 1
    assert len(payload["series"]) >= 1


def test_index_endpoint_returns_value(client):
    connector = get_connector("fixture_local")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/pokemon_demo.json")
        connector.run(session, "data/fixtures/prices_demo.json")
        session.commit()

    response = client.get("/api/v1/index?set_code=SV1&source=demo&currency=EUR", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["sample_size"] >= 1
    assert payload["value"] is not None
