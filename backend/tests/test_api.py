import os
import time

from sqlalchemy import func, select, text

from app import db
from app.auth import middleware
from app.auth.create_key import main as create_key_main
from app.auth.service import disable_key_by_prefix, hash_api_key, rotate_key_by_prefix
from app.ingest.registry import get_connector
from app.models import ApiKey, ApiPlan, Card, PriceSnapshot, Print, Product, SourceRecord
from app.scripts.reindex_search import rebuild_search_documents
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
    payload = response.get_json()
    assert payload["ok"] is True
    assert "revision" in payload


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
    if payload:
        assert payload[0]["variant"] == "default"


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
    assert payload["print"].get("variant") == "default"


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
    assert any(
        item["type"] == "print"
        and item.get("collector_number") == "001"
        and item.get("variant") == "default"
        for item in payload
    )


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




def test_admin_dev_api_keys_requires_header(client, monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    monkeypatch.setenv("FLASK_ENV", "development")

    response = client.post("/api/admin/dev/api-keys")
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_admin_token"}


def test_admin_dev_api_keys_rejects_invalid_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "super-secret")

    response = client.post("/api/admin/dev/api-keys", headers={"X-Admin-Token": "wrong-token"})
    assert response.status_code == 403
    assert response.get_json() == {"error": "invalid_admin_token"}


def test_admin_dev_api_keys_accepts_valid_token(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "super-secret")

    response = client.post("/api/admin/dev/api-keys", headers={"X-Admin-Token": "super-secret"})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["api_key"].startswith("ak_")



def test_admin_seed_allows_admin_scope(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")
    response = client.post("/api/admin/seed", headers=_auth_headers("admin-seed", ["read:catalog", "read:admin"]))
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert isinstance(payload["inserted"], int)

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
    assert set(payload.keys()) == {"sources", "games", "connectors", "runs", "newest_timestamps", "now"}
    assert isinstance(payload["sources"], list)
    assert isinstance(payload["games"], list)
    assert isinstance(payload["connectors"], list)
    assert isinstance(payload["runs"], list)
    assert isinstance(payload["newest_timestamps"], dict)

    if payload["sources"]:
        assert set(payload["sources"][0].keys()) == {"name", "last_run_at", "records"}
    if payload["games"]:
        assert set(payload["games"][0].keys()) == {"slug", "cards", "sets", "prints"}
    if payload["connectors"]:
        assert set(payload["connectors"][0].keys()) == {
            "name",
            "source_records_total",
            "newest_source_record_at",
            "newest_ingest_started_at",
            "newest_ingest_finished_at",
        }




def test_admin_ingest_status_forbids_without_admin_scope(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/v1/admin/ingest-status", headers=_auth_headers("catalog-only", ["read:catalog"]))
    assert response.status_code == 403
    assert response.get_json() == {"error": "insufficient_scope"}

def test_admin_ingest_status_requires_key(client):
    os.environ["PUBLIC_API_ENABLED"] = "false"
    response = client.get("/api/v1/admin/ingest-status")
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}




def test_prices_placeholder_returns_200_and_json(client):
    run_seed()
    response = client.get(
        "/api/v1/prices?game=pokemon&q=pika&source=dummy&limit=5",
        headers=_auth_headers(),
    )
    assert response.status_code == 200
    assert isinstance(response.get_json(), list)

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


def test_games_includes_yugioh_and_riftbound_after_ingest(client):
    run_seed()
    ygo_connector = get_connector("ygoprodeck_yugioh")
    rift_connector = get_connector("riftbound")

    with db.SessionLocal() as session:
        ygo_connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        rift_connector.run(session, "data/fixtures/riftbound_sample.json", fixture=True, incremental=False)
        session.commit()

    response = client.get("/api/v1/games", headers=_auth_headers())
    assert response.status_code == 200
    slugs = {item["slug"] for item in response.get_json()}
    assert "yugioh" in slugs
    assert "riftbound" in slugs


def test_search_returns_results_for_yugioh_after_fixture_ingest(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.commit()

    response = client.get("/api/v1/search?q=Dark&game=yugioh", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any("Dark Magician" in item["title"] for item in payload)


def test_search_dark_magician_yugioh_after_reindex_returns_card_and_print(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        session.execute(text("DELETE FROM search_documents"))
        rebuild_search_documents(session)
        session.commit()

    response = client.get("/api/v1/search?q=Dark%20Magician&game=yugioh", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["type"] == "card" and item["title"] == "Dark Magician" for item in payload)
    assert any(item["type"] == "print" and "LOB-005" in (item.get("subtitle") or "") for item in payload)


def test_search_dark_magician_exact_title_ranks_ahead_of_related_titles(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)

        yugioh_game_id = session.execute(text("SELECT id FROM games WHERE slug = 'yugioh' LIMIT 1")).scalar_one()
        set_id = session.execute(text("SELECT id FROM sets WHERE game_id = :game_id ORDER BY id LIMIT 1"), {"game_id": yugioh_game_id}).scalar_one()

        related_card_1 = Card(game_id=yugioh_game_id, name="Dark Magician Girl")
        related_card_2 = Card(game_id=yugioh_game_id, name="Dark Magician the Magician of Black Magic")
        session.add_all([related_card_1, related_card_2])
        session.flush()

        session.add_all(
            [
                Print(set_id=set_id, card_id=related_card_1.id, collector_number="LOB-006", rarity="Ultra Rare", variant="default"),
                Print(set_id=set_id, card_id=related_card_2.id, collector_number="LOB-007", rarity="Ultra Rare", variant="default"),
            ]
        )
        session.commit()

    with db.SessionLocal() as session:
        session.execute(text("DELETE FROM search_documents"))
        rebuild_search_documents(session)
        session.commit()

    response = client.get("/api/v1/search?q=Dark%20Magician&game=yugioh", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload

    def _index_of(title: str) -> int:
        return next(i for i, item in enumerate(payload) if item["title"] == title)

    exact_idx = _index_of("Dark Magician")
    girl_idx = _index_of("Dark Magician Girl")
    long_idx = _index_of("Dark Magician the Magician of Black Magic")

    assert exact_idx < girl_idx
    assert exact_idx < long_idx


def test_search_lob_005_prioritizes_exact_dark_magician_print(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        session.execute(text("DELETE FROM search_documents"))
        rebuild_search_documents(session)
        session.commit()

    response = client.get("/api/v1/search?q=LOB-005&game=yugioh&type=print", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload

    top = payload[0]
    assert top["type"] == "print"
    assert top["title"] == "Dark Magician"
    assert top["collector_number"] == "LOB-005"


def test_search_blue_eyes_white_dragon_exact_title_remains_top(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        session.execute(text("DELETE FROM search_documents"))
        rebuild_search_documents(session)
        session.commit()

    response = client.get("/api/v1/search?q=Blue-Eyes%20White%20Dragon&game=yugioh", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload

    assert payload[0]["title"] == "Blue-Eyes White Dragon"
    assert payload[0]["type"] == "card"

def test_reindex_populates_yugioh_search_documents_with_searchable_text(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.commit()

    with db.SessionLocal() as session:
        session.execute(text("DELETE FROM search_documents"))
        stats = rebuild_search_documents(session)
        session.commit()

    assert stats["cards"] >= 2
    assert stats["prints"] >= 2

    with db.SessionLocal() as session:
        docs = session.execute(
            text(
                """
                SELECT doc_type, title, subtitle, tsv
                FROM search_documents sd
                JOIN games g ON g.id = sd.game_id
                WHERE g.slug = 'yugioh'
                """
            )
        ).mappings().all()

    assert docs
    assert any(doc["doc_type"] == "card" and doc["title"] == "Dark Magician" for doc in docs)
    assert any(doc["doc_type"] == "print" and "LOB-005" in (doc.get("subtitle") or "") for doc in docs)
    assert all((doc.get("tsv") or "").strip() for doc in docs)


def test_search_falls_back_when_index_query_returns_empty_without_game_filter(client):
    connector = get_connector("ygoprodeck_yugioh")
    with db.SessionLocal() as session:
        connector.run(session, "data/fixtures/ygoprodeck_yugioh_sample.json", fixture=True, incremental=False)
        session.execute(text("DELETE FROM search_documents"))
        session.commit()

    response = client.get("/api/v1/search?q=Dark%20Magician", headers=_auth_headers())
    assert response.status_code == 200
    payload = response.get_json()
    assert payload
    assert any(item["title"] == "Dark Magician" for item in payload)


def test_admin_create_api_key_localhost(client):
    response = client.post('/api/admin/api-keys', headers={'Host': 'localhost'})
    assert response.status_code == 201
    payload = response.get_json()
    assert payload['api_key'].startswith('ak_')
    assert 'created_at' in payload
    assert 'expires_at' in payload


def test_admin_create_api_key_with_token_required(client, monkeypatch):
    monkeypatch.setenv('ADMIN_TOKEN', 'secret-token')

    forbidden_response = client.post('/api/admin/api-keys', headers={'Host': 'localhost'})
    assert forbidden_response.status_code == 403

    ok_response = client.post(
        '/api/admin/api-keys',
        headers={'Host': 'example.com', 'X-Admin-Token': 'secret-token'},
    )
    assert ok_response.status_code == 201




def test_admin_refresh_returns_accepted_quickly(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    def fake_run_daily_refresh(args):
        time.sleep(1.5)
        return {"exit_code": 0}

    class DeferredFuture:
        def result(self):
            return None

    def fake_submit(fn, *args, **kwargs):
        return DeferredFuture()

    monkeypatch.setattr("app.routes.admin_refresh.run_daily_refresh", fake_run_daily_refresh)
    monkeypatch.setattr("app.routes.admin_refresh._REFRESH_EXECUTOR.submit", fake_submit)

    headers = _auth_headers("admin-rf-fast", ["read:catalog", "read:admin"])
    started = time.perf_counter()
    response = client.post("/api/admin/refresh", headers=headers, json={"incremental": True})
    elapsed = time.perf_counter() - started

    assert response.status_code == 202
    assert elapsed < 1.0

def test_admin_refresh_requires_auth(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")
    response = client.post("/api/admin/refresh", json={})
    assert response.status_code == 401
    assert response.get_json() == {"error": "missing_api_key"}




def test_admin_refresh_limit_parsing_zero_skips_and_missing_remains_unset(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    captured = {}

    def fake_build_refresh_args(**kwargs):
        captured.update(kwargs)
        return object()

    class ImmediateFuture:
        def result(self):
            return None

    def fake_submit(fn, *args, **kwargs):
        return ImmediateFuture()

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", fake_build_refresh_args)
    monkeypatch.setattr("app.routes.admin_refresh._REFRESH_EXECUTOR.submit", fake_submit)

    headers = _auth_headers("admin-rf-limits", ["read:catalog", "read:admin"])
    response = client.post(
        "/api/admin/refresh",
        headers=headers,
        json={"pokemon_limit": 0, "yugioh_limit": 50, "incremental": True},
    )

    assert response.status_code == 202
    assert captured["pokemon_limit"] == 0
    assert captured["mtg_limit"] is None
    assert captured["yugioh_limit"] == 50
    assert captured["riftbound_limit"] is None




def test_admin_refresh_limit_parsing_yugioh_only_keeps_other_games_unset(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    captured = {}

    def fake_build_refresh_args(**kwargs):
        captured.update(kwargs)
        return object()

    class ImmediateFuture:
        def result(self):
            return None

    def fake_submit(fn, *args, **kwargs):
        return ImmediateFuture()

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", fake_build_refresh_args)
    monkeypatch.setattr("app.routes.admin_refresh._REFRESH_EXECUTOR.submit", fake_submit)

    headers = _auth_headers("admin-rf-ygo-only", ["read:catalog", "read:admin"])
    response = client.post(
        "/api/admin/refresh",
        headers=headers,
        json={"yugioh_limit": 200, "incremental": True},
    )

    assert response.status_code == 202
    assert captured["pokemon_limit"] is None
    assert captured["mtg_limit"] is None
    assert captured["yugioh_limit"] == 200
    assert captured["riftbound_limit"] is None
def test_admin_refresh_limit_parsing_null_uses_default(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    captured = {}

    def fake_build_refresh_args(**kwargs):
        captured.update(kwargs)
        return object()

    class ImmediateFuture:
        def result(self):
            return None

    def fake_submit(fn, *args, **kwargs):
        return ImmediateFuture()

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", fake_build_refresh_args)
    monkeypatch.setattr("app.routes.admin_refresh._REFRESH_EXECUTOR.submit", fake_submit)

    headers = _auth_headers("admin-rf-null", ["read:catalog", "read:admin"])
    response = client.post(
        "/api/admin/refresh",
        headers=headers,
        json={"pokemon_limit": None, "mtg_limit": None, "yugioh_limit": None, "riftbound_limit": None},
    )

    assert response.status_code == 202
    assert captured["pokemon_limit"] is None
    assert captured["mtg_limit"] is None
    assert captured["yugioh_limit"] is None
    assert captured["riftbound_limit"] is None
def test_admin_refresh_allows_admin_key(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    def fake_run_daily_refresh(args):
        return {
            "exit_code": 0,
            "pokemon": {"ok": True, "totals": {}},
            "mtg": {"ok": True, "totals": {}},
            "yugioh": {"ok": True, "totals": {}},
            "riftbound": {"ok": True, "totals": {}},
            "reindex": {"ok": True},
        }

    class ImmediateFuture:
        def result(self):
            return None

    def fake_submit(fn, *args, **kwargs):
        fn(*args, **kwargs)
        return ImmediateFuture()

    monkeypatch.setattr("app.routes.admin_refresh.run_daily_refresh", fake_run_daily_refresh)
    monkeypatch.setattr("app.routes.admin_refresh._REFRESH_EXECUTOR.submit", fake_submit)

    headers = _auth_headers("admin-rf", ["read:catalog", "read:admin"])
    response = client.post(
        "/api/admin/refresh",
        headers=headers,
        json={"pokemon_set": "base1", "pokemon_limit": 10, "incremental": True},
    )
    assert response.status_code == 202
    payload = response.get_json()
    assert payload["queued"] is True
    assert isinstance(payload["job_id"], str) and payload["job_id"]
    assert payload["status_url"] == "/api/v1/admin/ingest-status"



def test_admin_refresh_sync_calls_run_daily_refresh_and_returns_summary(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    captured = {}

    def fake_build_refresh_args(**kwargs):
        captured.update(kwargs)
        return "refresh-args"

    def fake_run_daily_refresh(args):
        assert args == "refresh-args"
        return {"exit_code": 0, "ok": True, "details": {"pokemon": 10}}

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", fake_build_refresh_args)
    monkeypatch.setattr("app.routes.admin_refresh.run_daily_refresh", fake_run_daily_refresh)

    headers = _auth_headers("admin-rf-sync-ok", ["read:catalog", "read:admin"])
    response = client.post(
        "/api/admin/refresh-sync",
        headers=headers,
        json={"pokemon_set": "base1", "pokemon_limit": 10, "incremental": True},
    )

    assert response.status_code == 200
    assert response.get_json() == {"exit_code": 0, "ok": True, "details": {"pokemon": 10}}
    assert captured["pokemon_set"] == "base1"
    assert captured["pokemon_limit"] == 10
    assert captured["incremental"] is True


def test_admin_refresh_sync_returns_500_when_refresh_fails(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", lambda **kwargs: object())
    monkeypatch.setattr("app.routes.admin_refresh.run_daily_refresh", lambda args: {"exit_code": 1, "error": "boom"})

    headers = _auth_headers("admin-rf-sync-fail", ["read:catalog", "read:admin"])
    response = client.post("/api/admin/refresh-sync", headers=headers, json={"incremental": True})

    assert response.status_code == 500
    assert response.get_json() == {"exit_code": 1, "error": "boom"}


def test_admin_refresh_sync_v1_alias(client, monkeypatch):
    monkeypatch.setenv("PUBLIC_API_ENABLED", "false")

    monkeypatch.setattr("app.routes.admin_refresh.build_refresh_args", lambda **kwargs: object())
    monkeypatch.setattr("app.routes.admin_refresh.run_daily_refresh", lambda args: {"exit_code": 0, "ok": True})

    headers = _auth_headers("admin-rf-sync-v1", ["read:catalog", "read:admin"])
    response = client.post("/api/v1/admin/refresh-sync", headers=headers, json={"incremental": True})

    assert response.status_code == 200
    assert response.get_json() == {"exit_code": 0, "ok": True}

def test_admin_search_debug_requires_token_when_configured(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    response = client.get("/api/v1/admin/search-debug", headers={"Host": "example.com", **_auth_headers("admin-search-debug", ["read:catalog", "read:admin"])})

    assert response.status_code == 403


def test_admin_reindex_search_runs_and_returns_stats(client, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "secret-token")

    monkeypatch.setattr("app.scripts.reindex_search.rebuild_search_documents", lambda session: {"cards": 1, "sets": 2, "prints": 3})

    response = client.post(
        "/api/v1/admin/reindex-search",
        headers={"Host": "example.com", "X-Admin-Token": "secret-token", **_auth_headers("admin-reindex-search", ["read:catalog", "read:admin"])},
    )

    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "reindex": {"cards": 1, "sets": 2, "prints": 3}}
